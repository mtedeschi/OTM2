# -*- coding: utf-8 -*-
from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division

import json
from random import shuffle
from datetime import datetime
import psycopg2

from django.test import TestCase
from django.db import connection
from django.db.models import Q
from django.core.exceptions import ValidationError

from django.contrib.gis.geos import Point, Polygon

from treemap.tests import (make_instance, make_commander_user,
                           make_officer_user,
                           set_write_permissions)

from treemap.udf import UserDefinedFieldDefinition
from treemap.models import Plot
from treemap.audit import (AuthorizeException, FieldPermission,
                           approve_or_reject_audit_and_apply,
                           approve_or_reject_audits_and_apply)


def make_collection_udf(instance, name='Stewardship', model='Plot',
                        datatype=None):
    # Need to setup the hstore extension to make UDFs
    psycopg2.extras.register_hstore(connection.cursor(), globally=True)

    if datatype is None:
        datatype = [
            {'type': 'string',
             'name': 'action'},
            {'type': 'int',
             'name': 'height'}]

    return UserDefinedFieldDefinition.objects.create(
        instance=instance,
        model_type=model,
        datatype=json.dumps(datatype),
        iscollection=True,
        name=name)


class ScalarUDFFilterTest(TestCase):
    def setUp(self):
        self.instance = make_instance()
        self.commander_user = make_commander_user(self.instance)
        set_write_permissions(self.instance, self.commander_user,
                              'Plot',
                              ['udf:Test choice', 'udf:Test string',
                               'udf:Test int', 'udf:Test date',
                               'udf:Test float'])

        self.p = Point(-8515941.0, 4953519.0)

        UserDefinedFieldDefinition.objects.create(
            instance=self.instance,
            model_type='Plot',
            datatype=json.dumps({'type': 'choice',
                                 'choices': ['a', 'b', 'c']}),
            iscollection=False,
            name='Test choice')

        UserDefinedFieldDefinition.objects.create(
            instance=self.instance,
            model_type='Plot',
            datatype=json.dumps({'type': 'string'}),
            iscollection=False,
            name='Test string')

        UserDefinedFieldDefinition.objects.create(
            instance=self.instance,
            model_type='Plot',
            datatype=json.dumps({'type': 'date'}),
            iscollection=False,
            name='Test date')

        UserDefinedFieldDefinition.objects.create(
            instance=self.instance,
            model_type='Plot',
            datatype=json.dumps({'type': 'int'}),
            iscollection=False,
            name='Test int')

        UserDefinedFieldDefinition.objects.create(
            instance=self.instance,
            model_type='Plot',
            datatype=json.dumps({'type': 'float'}),
            iscollection=False,
            name='Test float')

        self.plot = Plot(geom=self.p, instance=self.instance)
        self.plot.save_with_user(self.commander_user)

        psycopg2.extras.register_hstore(connection.cursor(), globally=True)

        def create_and_save_with_choice(c, n=1):
            plots = []
            for i in xrange(n):
                plot = Plot(geom=self.p, instance=self.instance)
                plot.udfs['Test choice'] = c
                plot.save_with_user(self.commander_user)
                plots.append(plot)

            return {plot.pk for plot in plots}

        self.choice_a = create_and_save_with_choice('a', n=2)
        self.choice_b = create_and_save_with_choice('b', n=3)
        self.choice_c = create_and_save_with_choice('c', n=7)

    def test_filtering_on_string_and_choice_using_count(self):
        plots = Plot.objects.filter(**{'udf:Test choice': 'a'})
        self.assertEqual(
            len(self.choice_a),
            plots.count())

    def test_filtering_on_value_works(self):
        plots = Plot.objects.filter(**{'udf:Test choice': 'b'})
        self.assertEqual(
            self.choice_b,
            {plot.pk for plot in plots})

    def test_combine_with_geom(self):
        plot_a = Plot.objects.get(pk=self.choice_a.pop())
        plot_b = Plot.objects.get(pk=self.choice_b.pop())

        p = Point(10, 0)

        poly = Polygon(((5, -5), (15, -5), (15, 5), (5, 5), (5, -5)))

        plot_a.geom = p
        plot_a.save_with_user(self.commander_user)

        plot_b.geom = p
        plot_b.save_with_user(self.commander_user)

        a_in_poly = Plot.objects.filter(**{'udf:Test choice': 'a'})\
                                .filter(geom__contained=poly)

        self.assertEqual({plot.pk for plot in a_in_poly},
                         {plot_a.pk, })

        b_in_poly = Plot.objects.filter(**{'udf:Test choice': 'b'})\
                                .filter(geom__contained=poly)

        self.assertEqual({plot.pk for plot in b_in_poly},
                         {plot_b.pk, })

    def test_search_suffixes(self):
        plot1 = Plot(geom=self.p, instance=self.instance)
        plot1.udfs['Test string'] = 'this is a test'
        plot1.save_with_user(self.commander_user)

        plot2 = Plot(geom=self.p, instance=self.instance)
        plot2.udfs['Test string'] = 'this is aLsO'
        plot2.save_with_user(self.commander_user)

        def run(sfx, val):
            return {plot.pk
                    for plot
                    in Plot.objects.filter(
                        **{'udf:Test string' + sfx: val})}

        self.assertEqual(set(), run('', 'also'))

        self.assertEqual({plot1.pk, plot2.pk},
                         run('__contains', 'this is a'))

        self.assertEqual({plot2.pk}, run('__icontains', 'this is al'))

    def _setup_dates(self):
        def create_plot_with_date(adate):
            plot = Plot(geom=self.p, instance=self.instance)
            plot.udfs['Test date'] = adate
            plot.save_with_user(self.commander_user)
            return plot

        dates = [
            (2010, 3, 4),
            (2010, 3, 5),
            (2010, 4, 4),
            (2010, 5, 5),
            (2012, 3, 4),
            (2012, 3, 5),
            (2012, 4, 4),
            (2012, 5, 5),
            (2013, 3, 4)]

        dates = [datetime(*adate) for adate in dates]

        # Get dates out of standard order
        shuffle(dates, lambda: 0.5)
        for adate in dates:
            create_plot_with_date(adate)

        return dates

    def test_date_ordering_normal(self):
        dates = self._setup_dates()
        plots = Plot.objects.filter(**{'udf:Test date__isnull': False})\
                            .order_by('MapFeature.udf:Test date')

        dates.sort()

        selected_dates = [plot.udfs['Test date']
                          for plot in plots]
        self.assertEqual(dates, selected_dates)

    def test_date_ordering_reverse(self):
        dates = self._setup_dates()
        plots = Plot.objects.filter(**{'udf:Test date__isnull': False})\
                            .order_by('-MapFeature.udf:Test date')

        dates.sort()
        dates.reverse()

        selected_dates = [plot.udfs['Test date']
                          for plot in plots]
        self.assertEqual(dates, selected_dates)

    def test_date_ordering_gt(self):
        self._setup_dates()
        adate = datetime(2011, 1, 1)

        plots = Plot.objects.filter(**{'udf:Test date__gt': adate})
        self.assertEqual(len(plots), 5)

        plots = Plot.objects.filter(**{'udf:Test date__lt': adate})
        self.assertEqual(len(plots), 4)

    def test_integer_gt_and_lte_constraints(self):
        def create_plot_with_num(anint):
            plot = Plot(geom=self.p, instance=self.instance)
            plot.udfs['Test int'] = anint
            plot.save_with_user(self.commander_user)
            return plot

        for i in xrange(0, 7):
            create_plot_with_num(i)

        plots = Plot.objects.filter(**{'udf:Test int__gt': 2,
                                       'udf:Test int__lte': 4})
        self.assertEqual(len(plots), 2)

    def test_float_gt_and_lte_constraints(self):
        def create_plot_with_num(afloat):
            plot = Plot(geom=self.p, instance=self.instance)
            plot.udfs['Test float'] = afloat
            plot.save_with_user(self.commander_user)
            return plot

        # creates 1.0 through 3.0 moving by tenths
        for i in xrange(10, 30):
            create_plot_with_num(float(i)/10.0)

        plots = Plot.objects.filter(**{'udf:Test float__gt': 1.5,
                                       'udf:Test float__lte': 2.0})

        self.assertEqual(len(plots), 5)  # 1.6, 1.7, 1.8, 1.9, 2.0

    def test_using_q_objects(self):
        qb = Q(**{'udf:Test choice': 'b'})
        qc = Q(**{'udf:Test choice': 'c'})

        q = qb | qc

        plots = Plot.objects.filter(q)

        self.assertEqual(
            self.choice_b | self.choice_c,
            {plot.pk for plot in plots})


class UDFAuditTest(TestCase):
    def setUp(self):
        self.instance = make_instance()
        self.commander_user = make_commander_user(self.instance)
        set_write_permissions(self.instance, self.commander_user,
                              'Plot', ['udf:Test choice'])

        self.p = Point(-8515941.0, 4953519.0)

        UserDefinedFieldDefinition.objects.create(
            instance=self.instance,
            model_type='Plot',
            datatype=json.dumps({'type': 'choice',
                                 'choices': ['a', 'b', 'c']}),
            iscollection=False,
            name='Test choice')

        UserDefinedFieldDefinition.objects.create(
            instance=self.instance,
            model_type='Plot',
            datatype=json.dumps({'type': 'string'}),
            iscollection=False,
            name='Test unauth')

        UserDefinedFieldDefinition.objects.create(
            instance=self.instance,
            model_type='Plot',
            datatype=json.dumps([{'type': 'choice',
                                  'name': 'a choice',
                                  'choices': ['a', 'b', 'c']},
                                 {'type': 'string',
                                  'name': 'a string'}]),
            iscollection=True,
            name='Test collection')

        self.plot = Plot(geom=self.p, instance=self.instance)
        self.plot.save_with_user(self.commander_user)

        psycopg2.extras.register_hstore(connection.cursor(), globally=True)

    def test_mask_unauthorized_with_udfs(self):
        officer_user = make_officer_user(self.instance)

        self.plot.udfs['Test choice'] = 'b'
        self.plot.save_with_user(self.commander_user)
        self.plot.udfs['Test unauth'] = 'foo'
        self.plot.save_base()

        newplot = Plot.objects.get(pk=self.plot.pk)
        self.assertEqual(newplot.udfs['Test choice'], 'b')
        self.assertEqual(newplot.udfs['Test unauth'], 'foo')

        newplot = Plot.objects.get(pk=self.plot.pk)
        newplot.mask_unauthorized_fields(self.commander_user)
        self.assertEqual(newplot.udfs['Test choice'], 'b')
        self.assertEqual(newplot.udfs['Test unauth'], None)

        newplot = Plot.objects.get(pk=self.plot.pk)
        newplot.mask_unauthorized_fields(officer_user)
        self.assertEqual(newplot.udfs['Test choice'], None)
        self.assertEqual(newplot.udfs['Test unauth'], None)

    def test_update_field_creates_audit(self):
        self.plot.udfs['Test choice'] = 'b'
        self.plot.save_with_user(self.commander_user)

        last_audit = list(self.plot.audits())[-1]

        self.assertEqual(last_audit.model, 'Plot')
        self.assertEqual(last_audit.model_id, self.plot.pk)
        self.assertEqual(last_audit.field, 'udf:Test choice')
        self.assertEqual(last_audit.previous_value, None)
        self.assertEqual(last_audit.current_value, 'b')

        self.plot.udfs['Test choice'] = 'c'
        self.plot.save_with_user(self.commander_user)

        last_audit = list(self.plot.audits())[-1]

        self.assertEqual(last_audit.model, 'Plot')
        self.assertEqual(last_audit.model_id, self.plot.pk)
        self.assertEqual(last_audit.field, 'udf:Test choice')
        self.assertEqual(last_audit.previous_value, 'b')
        self.assertEqual(last_audit.current_value, 'c')

    def test_cant_edit_unauthorized_collection(self):
        self.plot.udfs['Test collection'] = [
            {'a choice': 'a', 'a string': 's'}]

        self.assertRaises(AuthorizeException,
                          self.plot.save_with_user, self.commander_user)

    def test_cant_edit_unauthorized_field(self):
        self.plot.udfs['Test unauth'] = 'c'
        self.assertRaises(AuthorizeException,
                          self.plot.save_with_user, self.commander_user)

    def test_create_and_apply_pending(self):
        pending = self.plot.audits().filter(requires_auth=True)

        self.assertEqual(len(pending), 0)

        role = self.commander_user.get_role(self.instance)
        fp, _ = FieldPermission.objects.get_or_create(
            model_name='Plot', field_name='udf:Test unauth',
            permission_level=FieldPermission.WRITE_WITH_AUDIT,
            role=role, instance=self.instance)

        self.plot.udfs['Test unauth'] = 'c'
        self.plot.save_with_user(self.commander_user)

        reloaded_plot = Plot.objects.get(pk=self.plot.pk)

        self.assertEqual(
            reloaded_plot.udfs['Test unauth'],
            None)

        pending = self.plot.audits().filter(requires_auth=True)

        self.assertEqual(len(pending), 1)

        fp.permission_level = FieldPermission.WRITE_DIRECTLY
        fp.save()

        approve_or_reject_audit_and_apply(pending[0],
                                          self.commander_user,
                                          True)

        reloaded_plot = Plot.objects.get(pk=self.plot.pk)

        self.assertEqual(
            reloaded_plot.udfs['Test unauth'],
            'c')

    def test_create_invalid_pending_collection(self):
        pending = self.plot.audits().filter(requires_auth=True)

        self.assertEqual(len(pending), 0)

        role = self.commander_user.get_role(self.instance)
        fp, _ = FieldPermission.objects.get_or_create(
            model_name='Plot', field_name='udf:Test collection',
            permission_level=FieldPermission.WRITE_WITH_AUDIT,
            role=role, instance=self.instance)

        self.plot.udfs['Test collection'] = [
            {'a choice': 'invalid choice', 'a string': 's'}]
        self.assertRaises(ValidationError,
                          self.plot.save_with_user, self.commander_user)

    def test_create_and_apply_pending_collection(self):
        pending = self.plot.audits().filter(requires_auth=True)

        self.assertEqual(len(pending), 0)

        role = self.commander_user.get_role(self.instance)
        fp, _ = FieldPermission.objects.get_or_create(
            model_name='Plot', field_name='udf:Test collection',
            permission_level=FieldPermission.WRITE_WITH_AUDIT,
            role=role, instance=self.instance)

        self.plot.udfs['Test collection'] = [
            {'a choice': 'a', 'a string': 's'}]
        self.plot.save_with_user(self.commander_user)

        reloaded_plot = Plot.objects.get(pk=self.plot.pk)

        self.assertEqual(
            reloaded_plot.udfs['Test collection'],
            [])

        pending = self.plot.audits().filter(requires_auth=True)

        # Expecting 'model_id', 'id', 'field def id'
        # and two udf fields ('a string' and 'a choice')
        self.assertEqual(len(pending), 5)

        fp.permission_level = FieldPermission.WRITE_DIRECTLY
        fp.save()

        approve_or_reject_audits_and_apply(pending,
                                           self.commander_user,
                                           True)

        reloaded_plot = Plot.objects.get(pk=self.plot.pk)

        col = reloaded_plot.udfs['Test collection']
        self.assertEqual(len(col), 1)
        self.assertEqual(col[0]['a choice'], 'a')
        self.assertEqual(col[0]['a string'], 's')


class ScalarUDFDefTest(TestCase):

    def setUp(self):
        self.instance = make_instance()

    def _create_and_save_with_datatype(
            self, d, model_type='Plot', name='Blah', iscollection=False):
        return UserDefinedFieldDefinition.objects.create(
            instance=self.instance,
            model_type=model_type,
            datatype=json.dumps(d),
            iscollection=iscollection,
            name=name)

    def test_cannot_create_datatype_with_invalid_model(self):
        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype,
            {'type': 'string'},
            model_type='InvalidModel')

    def test_cannot_create_datatype_with_nonudf(self):
        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype,
            {'type': 'string'},
            model_type='InstanceUser')

    def test_cannot_create_duplicate_udfs(self):
        self._create_and_save_with_datatype(
            {'type': 'string'},
            name='random')

        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype,
            {'type': 'string'},
            name='random')

        self._create_and_save_with_datatype(
            {'type': 'string'},
            name='random2')

    def test_cannot_create_datatype_with_existing_field(self):
        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype,
            {'type': 'string'},
            name='width')

        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype,
            {'type': 'string'},
            name='id')

        self._create_and_save_with_datatype(
            {'type': 'string'},
            name='random')

    def test_must_have_type_key(self):
        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype, {})

    def test_invalid_type(self):
        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype, {'type': 'woohoo'})

        self._create_and_save_with_datatype({'type': 'float'})

    def test_description_op(self):
        self._create_and_save_with_datatype(
            {'type': 'float',
             'description': 'this is a float field'})

    def test_choices_not_empty_or_missing(self):
        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype,
            {'type': 'choice'})

        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype,
            {'type': 'choice',
             'choices': []})

        self._create_and_save_with_datatype(
            {'type': 'choice',
             'choices': ['a choice', 'another']})

    def test_cannot_create_choices_with_numeric_values(self):
        with self.assertRaises(ValidationError):
            self._create_and_save_with_datatype(
                {'type': 'choice',
                 'choices': [0, 1, 3, 4, 5]})

    def test_can_create_subfields(self):
        self._create_and_save_with_datatype(
            [{'type': 'choice',
              'name': 'achoice',
              'choices': ['a', 'b']},
             {'type': 'string',
              'name': 'something'}], iscollection=True)

    def test_must_have_name_on_subfields(self):
        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype,
            [{'type': 'choice',
              'choices': ['a', 'b']},
             {'type': 'string',
              'name': 'something'}],
            iscollection=True)

        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype,
            [{'type': 'choice',
              'choices': ['a', 'b'],
              'name': ''},
             {'type': 'string',
              'name': 'something'}],
            iscollection=True)

        self._create_and_save_with_datatype(
            [{'type': 'choice',
              'name': 'valid name',
              'choices': ['a', 'b']},
             {'type': 'string',
              'name': 'something'}],
            iscollection=True)

    def test_subfields_may_not_have_duplicate_names(self):
        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype,
            [{'type': 'choice',
              'name': 'valid name',
              'choices': ['a', 'b']},
             {'type': 'string',
              'name': 'valid name'}],
            name='another',
            iscollection=True)

        self._create_and_save_with_datatype(
            [{'type': 'choice',
              'name': 'valid name',
              'choices': ['a', 'b']},
             {'type': 'string',
              'name': 'valid name2'}],
            iscollection=True)

    def test_iscollection_requires_json_array(self):
        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype,
            [{'type': 'choice',
              'name': 'a name',
              'choices': ['a', 'b']},
             {'type': 'string',
              'name': 'something'}],
            iscollection=False)

        self._create_and_save_with_datatype(
            [{'type': 'choice',
              'choices': ['a', 'b'],
              'name': 'a name'},
             {'type': 'string',
              'name': 'something'}],
            iscollection=True)

    def test_not_iscollection_requires_only_a_dict(self):
        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype,
            {'type': 'choice',
             'choices': ['a', 'b']},
            iscollection=True)

        self._create_and_save_with_datatype(
            {'type': 'choice',
             'choices': ['a', 'b']},
            iscollection=False)

    def test_subfield_cannot_be_called_id(self):
        self.assertRaises(
            ValidationError,
            self._create_and_save_with_datatype,
            [{'type': 'choice',
              'name': 'id',
              'choices': ['a', 'b']},
             {'type': 'string',
              'name': 'something'}],
            iscollection=True)

        self._create_and_save_with_datatype(
            [{'type': 'choice',
              'name': 'anything else',
              'choices': ['a', 'b']},
             {'type': 'string',
              'name': 'something'}],
            iscollection=True)


class ScalarUDFTest(TestCase):

    def setUp(self):
        self.instance = make_instance()
        self.p = Point(-8515941.0, 4953519.0)

        def make_and_save_type(dtype):
            UserDefinedFieldDefinition.objects.create(
                instance=self.instance,
                model_type='Plot',
                datatype=json.dumps({'type': dtype}),
                iscollection=False,
                name='Test %s' % dtype)

        allowed_types = 'float', 'int', 'string', 'user', 'date'

        addl_fields = ['udf:Test %s' % ttype for ttype in allowed_types]
        addl_fields.append('udf:Test choice')

        self.commander_user = make_commander_user(self.instance)
        set_write_permissions(self.instance, self.commander_user,
                              'Plot', addl_fields)

        for dtype in allowed_types:
            make_and_save_type(dtype)

        UserDefinedFieldDefinition.objects.create(
            instance=self.instance,
            model_type='Plot',
            datatype=json.dumps({'type': 'choice',
                                 'choices': ['a', 'b', 'c']}),
            iscollection=False,
            name='Test choice')

        self.plot = Plot(geom=self.p, instance=self.instance)
        self.plot.save_with_user(self.commander_user)

        psycopg2.extras.register_hstore(connection.cursor(), globally=True)

    def _test_datatype(self, field, value):
        self.plot.udfs[field] = value
        self.plot.save_with_user(self.commander_user)

        self.plot = Plot.objects.get(pk=self.plot.pk)

        self.assertEqual(
            self.plot.udfs[field], value)

    def test_int_datatype(self):
        self._test_datatype('Test int', 4)

    def test_int_validation_non_integer(self):
        self.assertRaises(ValidationError,
                          self._test_datatype, 'Test int', 42.3)

        self.assertRaises(ValidationError,
                          self._test_datatype, 'Test int', 'blah')

    def test_float_datatype(self):
        self._test_datatype('Test float', 4.4)

    def test_float_validation(self):
        self.assertRaises(ValidationError,
                          self._test_datatype, 'Test float', 'blah')

    def test_choice_datatype(self):
        self._test_datatype('Test choice', 'a')

    def test_choice_validation(self):
        self.assertRaises(ValidationError,
                          self._test_datatype, 'Test choice', 'bad choice')

    def test_user_datatype(self):
        self._test_datatype('Test user', self.commander_user)

    def test_date_datatype(self):
        d = datetime.now().replace(microsecond=0)

        self._test_datatype('Test date', d)

    def test_string_datatype(self):
        self._test_datatype('Test string', 'Sweet Plot')

    def test_user_validation_invalid_id(self):
        self.assertRaises(ValidationError,
                          self._test_datatype, 'Test user', 349949)

    def test_user_validation_non_integer(self):
        self.assertRaises(ValidationError,
                          self._test_datatype, 'Test user', 'zztop')

    def test_in_operator(self):
        self.assertEqual('Test string' in self.plot.udfs,
                         True)
        self.assertEqual('RanDoM NAme' in self.plot.udfs,
                         False)

    def test_returns_none_for_empty_but_valid_udfs(self):
        self.assertEqual(self.plot.udfs['Test string'],
                         None)

    def test_raises_keyerror_for_invalid_udf(self):
        self.assertRaises(KeyError,
                          lambda: self.plot.udfs['RaNdoName'])


class CollectionUDFTest(TestCase):

    def setUp(self):
        self.instance = make_instance()
        self.p = Point(-8515941.0, 4953519.0)

        make_collection_udf(self.instance, 'Stewardship')

        self.commander_user = make_commander_user(self.instance)
        set_write_permissions(self.instance, self.commander_user,
                              'Plot', ['udf:Stewardship'])

        self.plot = Plot(geom=self.p, instance=self.instance)
        self.plot.save_with_user(self.commander_user)

    def test_can_get_and_set(self):
        stews = [{'action': 'water',
                  'height': 42},
                 {'action': 'prune',
                  'height': 12}]

        self.plot.udfs['Stewardship'] = stews

        self.plot.save_with_user(self.commander_user)

        reloaded_plot = Plot.objects.get(pk=self.plot.pk)

        new_stews = reloaded_plot.udfs['Stewardship']

        for expected_stew, actual_stew in zip(stews, new_stews):
            self.assertIn('id', actual_stew)

            self.assertDictContainsSubset(expected_stew, actual_stew)

    def test_can_delete(self):
        stews = [{'action': 'water',
                  'height': 42},
                 {'action': 'prune',
                  'height': 12}]

        self.plot.udfs['Stewardship'] = stews
        self.plot.save_with_user(self.commander_user)

        reloaded_plot = Plot.objects.get(pk=self.plot.pk)
        all_new_stews = reloaded_plot.udfs['Stewardship']

        # Remove first one
        new_stews = all_new_stews[1:]
        reloaded_plot.udfs['Stewardship'] = new_stews
        reloaded_plot.save_with_user(self.commander_user)

        reloaded_plot = Plot.objects.get(pk=self.plot.pk)
        newest_stews = reloaded_plot.udfs['Stewardship']

        self.assertEqual(len(newest_stews), 1)
        self.assertEqual(newest_stews[0]['action'], 'prune')
        self.assertEqual(newest_stews[0]['height'], 12)

    # Collection fields used the same validation logic as scalar
    # udfs the point of this section is prove that the code is hooked
    # up, not to exhaustively test datatype validation
    def test_cannot_save_with_invalid_field_name(self):
        self.plot.udfs['Stewardship'] = [
            {'action': 'water',
             'height': 32,
             'random': 'test'}]

        self.assertRaises(
            ValidationError,
            self.plot.save_with_user,
            self.commander_user)

    def test_cannot_save_with_invalid_value(self):
        self.plot.udfs['Stewardship'] = [
            {'action': 'water',
             'height': 'too high'}]

        self.assertRaises(
            ValidationError,
            self.plot.save_with_user,
            self.commander_user)
