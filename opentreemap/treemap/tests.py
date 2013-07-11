from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division

import itertools

from django.test import TestCase
from django.test.client import RequestFactory
from treemap.models import Tree, Instance, Plot, User, Species, Role, FieldPermission, ReputationMetric, Boundary
from treemap.audit import Audit, AuditException, UserTrackingException, AuthorizeException
from treemap.views import audits, boundary_to_geojson, boundary_autocomplete
from django.contrib.gis.geos import Point, MultiPolygon, Polygon
from django.core.exceptions import FieldError

from audit import approve_or_reject_audit_and_apply

import json

######################################
## SETUP FUNCTIONS
######################################

def make_simple_polygon(n=1):
    """
    Creates a simple, point-like polygon for testing distances
    so it will save to the geom field on a Boundary.

    The idea is to create small polygons such that the n-value
    that is passed in can identify how far the polygon will be
    from the origin.

    For example:
    p1 = make_simple_polygon(1)
    p2 = make_simple_polygon(2)

    p1 will be closer to the origin.
    """
    return Polygon( ((n, n), (n, n+1), (n+1, n+1), (n, n)) )

def _make_loaded_role(instance, name, rep_thresh, permissions):
    role, created = Role.objects.get_or_create(name=name, instance=instance, rep_thresh=rep_thresh)
    role.save()

    for perm in permissions:
        model_name, field_name, permission_level = perm
        FieldPermission.objects.get_or_create(model_name=model_name, field_name=field_name,
                        permission_level=permission_level, role=role,
                        instance=instance)
    return role

def make_commander_role(instance):
    permissions = (
        ('Plot', 'geom', FieldPermission.WRITE_DIRECTLY),
        ('Plot', 'width', FieldPermission.WRITE_DIRECTLY),
        ('Plot', 'length', FieldPermission.WRITE_DIRECTLY),
        ('Plot', 'address_street', FieldPermission.WRITE_DIRECTLY),
        ('Plot', 'address_city', FieldPermission.WRITE_DIRECTLY),
        ('Plot', 'address_zip', FieldPermission.WRITE_DIRECTLY),
        ('Plot', 'import_event', FieldPermission.WRITE_DIRECTLY),
        ('Plot', 'owner_orig_id', FieldPermission.WRITE_DIRECTLY),
        ('Plot', 'readonly', FieldPermission.WRITE_DIRECTLY),
        ('Tree', 'plot', FieldPermission.WRITE_DIRECTLY),
        ('Tree', 'species', FieldPermission.WRITE_DIRECTLY),
        ('Tree', 'import_event', FieldPermission.WRITE_DIRECTLY),
        ('Tree', 'readonly', FieldPermission.WRITE_DIRECTLY),
        ('Tree', 'diameter', FieldPermission.WRITE_DIRECTLY),
        ('Tree', 'height', FieldPermission.WRITE_DIRECTLY),
        ('Tree', 'canopy_height', FieldPermission.WRITE_DIRECTLY),
        ('Tree', 'date_planted', FieldPermission.WRITE_DIRECTLY),
        ('Tree', 'date_removed', FieldPermission.WRITE_DIRECTLY))
    return _make_loaded_role(instance, 'commander', 3, permissions)

def make_officer_role(instance):
    permissions = (
        ('Plot', 'geom', FieldPermission.WRITE_DIRECTLY),
        ('Plot', 'length', FieldPermission.WRITE_DIRECTLY),
        ('Plot', 'readonly', FieldPermission.WRITE_DIRECTLY),
        ('Tree', 'diameter', FieldPermission.WRITE_DIRECTLY),
        ('Tree', 'plot', FieldPermission.WRITE_DIRECTLY),
        ('Tree', 'height', FieldPermission.WRITE_DIRECTLY))
    return _make_loaded_role(instance, 'officer', 3, permissions)

def make_apprentice_role(instance):
    permissions = (
        ('Plot', 'geom', FieldPermission.WRITE_WITH_AUDIT),
        ('Plot', 'width', FieldPermission.WRITE_WITH_AUDIT),
        ('Plot', 'length', FieldPermission.WRITE_WITH_AUDIT),
        ('Plot', 'address_street', FieldPermission.WRITE_WITH_AUDIT),
        ('Plot', 'address_city', FieldPermission.WRITE_WITH_AUDIT),
        ('Plot', 'address_zip', FieldPermission.WRITE_WITH_AUDIT),
        ('Plot', 'import_event', FieldPermission.WRITE_WITH_AUDIT),
        ('Plot', 'owner_orig_id', FieldPermission.WRITE_WITH_AUDIT),
        ('Plot', 'readonly', FieldPermission.WRITE_WITH_AUDIT),
        ('Tree', 'plot', FieldPermission.WRITE_WITH_AUDIT),
        ('Tree', 'species', FieldPermission.WRITE_WITH_AUDIT),
        ('Tree', 'import_event', FieldPermission.WRITE_WITH_AUDIT),
        ('Tree', 'readonly', FieldPermission.WRITE_WITH_AUDIT),
        ('Tree', 'diameter', FieldPermission.WRITE_WITH_AUDIT),
        ('Tree', 'height', FieldPermission.WRITE_WITH_AUDIT),
        ('Tree', 'canopy_height', FieldPermission.WRITE_WITH_AUDIT),
        ('Tree', 'date_planted', FieldPermission.WRITE_WITH_AUDIT),
        ('Tree', 'date_removed', FieldPermission.WRITE_WITH_AUDIT))
    return _make_loaded_role(instance, 'apprentice', 2, permissions)

def make_observer_role(instance):
    permissions = (
        ('Plot', 'geom', FieldPermission.READ_ONLY),
        ('Plot', 'length', FieldPermission.READ_ONLY),
        ('Tree', 'diameter', FieldPermission.READ_ONLY),
        ('Tree', 'height', FieldPermission.READ_ONLY))
    return _make_loaded_role(instance, 'observer', 2, permissions)

def make_instance():
    global_role, _ = Role.objects.get_or_create(name='global', rep_thresh=0)

    p1 = Point(0, 0)

    instance, _ = Instance.objects.get_or_create(
        name='i1',geo_rev=0,center=p1,default_role=global_role)
    return instance

def make_system_user():
    try:
        system_user = User.objects.get(username="system_user")
    except Exception:
        system_user = User(username="system_user")
        system_user.save_base()
    return system_user

def make_basic_user(instance, username):
    """ A helper function for making an instance and user

    You'll still want to load the permissions you need for each
    test onto the user's role. """
    system_user = make_system_user()

    user = User(username=username)
    user.save_with_user(system_user)
    return user

def make_instance_and_basic_user():
    instance = make_instance()
    basic_user = make_basic_user(instance, "custom_user")
    return instance, basic_user

def make_instance_and_system_user():
    instance = make_instance()
    system_user = make_system_user()
    return instance, system_user


######################################
## TESTS
######################################

class HashModelTest(TestCase):
    def setUp(self):
        self.instance, self.user = make_instance_and_basic_user()
        permissions = (
            ('Plot', 'geom', FieldPermission.WRITE_DIRECTLY),
            ('Plot', 'width', FieldPermission.WRITE_DIRECTLY),
            ('Plot', 'length', FieldPermission.WRITE_DIRECTLY),
            ('Plot', 'address_street', FieldPermission.WRITE_DIRECTLY),
            ('Tree', 'plot', FieldPermission.WRITE_DIRECTLY),
            ('Tree', 'readonly', FieldPermission.WRITE_DIRECTLY))
        self.user.roles.add(_make_loaded_role(self.instance, "custom", 0, permissions))

        self.p1 = Point(-8515941.0, 4953519.0)
        self.p2 = Point(-7615441.0, 5953519.0)

    def test_changing_fields_changes_hash(self):
        plot = Plot(geom=self.p1, instance=self.instance, created_by=self.user)
        plot.save_with_user(self.user)

        #
        # Make sure regular field updates change the hash
        #
        h1 = plot.hash
        plot.width = 44
        plot.save_with_user(self.user)
        h2 = plot.hash

        self.assertNotEqual(h1, h2, "Hashes should change")

        h1 = plot.hash
        plot.address_street = "test"
        plot.save_with_user(self.user)
        h2 = plot.hash

        self.assertNotEqual(h1, h2, "Hashes should change")

        #
        # Verify adding a new tree updates the plot hash
        #

        h1 = plot.hash
        tree = Tree(plot=plot,
                    instance=self.instance,
                    readonly=False,
                    created_by=self.user)
        tree.save_with_user(self.user)
        h2 = plot.hash

        self.assertNotEqual(h1, h2, "Hashes should change")

        #
        # Verify that updating a tree related to a plot also
        # changes the plot hash
        #

        h1 = plot.hash
        tree.readonly = True
        tree.save_with_user(self.user)

        h2 = plot.hash

        self.assertNotEqual(h1, h2, "Hashes should change")

class GeoRevIncr(TestCase):
    def setUp(self):
        self.p1 = Point(-8515941.0, 4953519.0)
        self.p2 = Point(-7615441.0, 5953519.0)
        self.instance, self.user = make_instance_and_basic_user()
        permissions = (
            ('Plot', 'geom', FieldPermission.WRITE_DIRECTLY),
            ('Plot', 'width', FieldPermission.WRITE_DIRECTLY),
            ('Plot', 'length', FieldPermission.WRITE_DIRECTLY),
            ('Plot', 'address_street', FieldPermission.WRITE_DIRECTLY),
            ('Plot', 'address_city', FieldPermission.WRITE_DIRECTLY),
            ('Plot', 'address_zip', FieldPermission.WRITE_DIRECTLY),
            ('Plot', 'import_event', FieldPermission.WRITE_DIRECTLY),
            ('Plot', 'owner_orig_id', FieldPermission.WRITE_DIRECTLY),
            ('Plot', 'readonly', FieldPermission.WRITE_DIRECTLY))
        self.user.roles.add(_make_loaded_role(self.instance, "custom", 0, permissions))

    def hash_and_rev(self):
        i = Instance.objects.get(pk=self.instance.pk)
        return [i.geo_rev_hash, i.geo_rev]

    def test_changing_geometry_updates_counter(self):
        rev1h, rev1 = self.hash_and_rev()

        # Create
        plot1 = Plot(geom=self.p1, instance=self.instance, created_by=self.user)
        plot1.save_with_user(self.user)

        rev2h, rev2 = self.hash_and_rev()

        self.assertNotEqual(rev1h, rev2h)
        self.assertEqual(rev1+1,rev2)

        plot2 = Plot(geom=self.p2, instance=self.instance, created_by=self.user)
        plot2.save_with_user(self.user)

        rev3h, rev3 = self.hash_and_rev()

        self.assertNotEqual(rev2h, rev3h)
        self.assertEqual(rev2+1,rev3)

        # Update
        plot2.geom = self.p1
        plot2.save_with_user(self.user)

        rev4h, rev4 = self.hash_and_rev()

        self.assertNotEqual(rev3h, rev4h)
        self.assertEqual(rev3+1,rev4)

        # Delete
        plot2.delete_with_user(self.user)

        rev5h, rev5 = self.hash_and_rev()

        self.assertNotEqual(rev4h, rev5h)
        self.assertEqual(rev4+1,rev5)

class UserRoleFieldPermissionTest(TestCase):
    def setUp(self):
        self.p1 = Point(-8515941.0, 4953519.0)
        self.instance, system_user = make_instance_and_system_user()

        self.outlaw_role = Role(name='outlaw', instance=self.instance, rep_thresh=1)
        self.outlaw_role.save()

        self.commander = User(username="commander")
        self.commander.save_with_user(system_user)
        self.commander.roles.add(make_commander_role(self.instance))

        self.officer = User(username="officer")
        self.officer.save_with_user(system_user)
        self.officer.roles.add(make_officer_role(self.instance))

        self.observer = User(username="observer")
        self.observer.save_with_user(system_user)
        self.observer.roles.add(make_observer_role(self.instance))

        self.outlaw = User(username="outlaw")
        self.outlaw.save_with_user(system_user)
        self.outlaw.roles.add(self.outlaw_role)

        self.anonymous = User(username="")
        self.anonymous.save_with_user(system_user)

        self.plot = Plot(geom=self.p1, instance=self.instance, created_by=self.officer)
        self.plot.save_with_user(self.officer)

        self.tree = Tree(plot=self.plot, instance=self.instance, created_by=self.officer)
        self.tree.save_with_user(self.officer)

    def test_no_permission_cant_edit_object(self):
        self.plot.length = 10
        self.assertRaises(AuthorizeException, self.plot.save_with_user, self.outlaw)
        self.assertNotEqual(Plot.objects.get(pk=self.plot.pk).length, 10)

        self.tree.diameter = 10
        self.assertRaises(AuthorizeException, self.tree.save_with_user, self.outlaw)
        self.assertNotEqual(Tree.objects.get(pk=self.tree.pk).diameter, 10)

    def test_readonly_cant_edit_object(self):
        self.plot.length = 10
        self.assertRaises(AuthorizeException, self.plot.save_with_user, self.observer)
        self.assertNotEqual(Plot.objects.get(pk=self.plot.pk).length, 10)

        self.tree.diameter = 10
        self.assertRaises(AuthorizeException, self.tree.save_with_user, self.observer)
        self.assertNotEqual(Tree.objects.get(pk=self.tree.pk).diameter, 10)

    def test_writeperm_allows_write(self):
        self.plot.length = 10
        self.plot.save_with_user(self.officer)
        self.assertEqual(Plot.objects.get(pk=self.plot.pk).length, 10)

        self.tree.diameter = 10
        self.tree.save_with_user(self.officer)
        self.assertEqual(Tree.objects.get(pk=self.tree.pk).diameter, 10)

    def test_save_new_object_authorized(self):
        '''Save two new objects with authorized user, nothing should happen'''
        plot = Plot(geom=self.p1, instance=self.instance, created_by=self.officer)
        plot.save_with_user(self.officer)
        tree = Tree(plot=plot, instance=self.instance, created_by=self.officer)
        tree.save_with_user(self.officer)

    def test_save_new_object_unauthorized(self):
        plot = Plot(geom=self.p1, instance=self.instance, created_by=self.outlaw)
        self.assertRaises(AuthorizeException, plot.save_with_user, self.outlaw)
        tree = Tree(plot=plot, instance=self.instance, created_by=self.outlaw)
        self.assertRaises(AuthorizeException, tree.save_with_user, self.outlaw)

    def test_delete_object(self):
        self.assertRaises(AuthorizeException, self.tree.delete_with_user, self.outlaw)
        self.assertRaises(AuthorizeException, self.plot.delete_with_user, self.outlaw)

        self.assertRaises(AuthorizeException, self.tree.delete_with_user, self.officer)
        self.assertRaises(AuthorizeException, self.plot.delete_with_user, self.officer)

        self.tree.delete_with_user(self.commander)
        self.plot.delete_with_user(self.commander)

    def test_clobbering_authorized(self):
        "When clobbering with a superuser, nothing should happen"
        self.plot.width = 5
        self.plot.save_with_user(self.commander)

        plot = Plot.objects.get(pk=self.plot.pk)
        plot.clobber_unauthorized(self.commander)
        self.assertEqual(self.plot.width, plot.width)

    def test_clobbering_unauthorized(self):
        "Clobbering changes an unauthorized field to None"
        self.plot.width = 5
        self.plot.save_base()

        plot = Plot.objects.get(pk=self.plot.pk)
        plot.clobber_unauthorized(self.observer)
        self.assertEqual(None, plot.width)

        plot = Plot.objects.get(pk=self.plot.pk)
        plot.clobber_unauthorized(self.outlaw)
        self.assertEqual(None, plot.width)

    def test_clobbering_whole_queryset(self):
        "Clobbering also works on entire querysets"
        self.plot.width = 5
        self.plot.save_base()

        plots = Plot.objects.filter(pk=self.plot.pk)
        plot = Plot.clobber_queryset(plots, self.observer)[0]
        self.assertEqual(None, plot.width)

    def test_write_fails_if_any_fields_cant_be_written(self):
        """ If a user tries to modify several fields simultaneously,
        only some of which s/he has access to, the write will fail
        for all fields."""
        self.plot.length = 10
        self.plot.width = 110
        self.assertRaises(AuthorizeException, self.plot.save_with_user, self.officer)
        self.assertNotEqual(Plot.objects.get(pk=self.plot.pk).length, 10)
        self.assertNotEqual(Plot.objects.get(pk=self.plot.pk).width, 110)

        self.tree.diameter = 10
        self.tree.canopy_height = 110
        self.assertRaises(AuthorizeException, self.tree.save_with_user, self.officer)
        self.assertNotEqual(Tree.objects.get(pk=self.tree.pk).diameter, 10)
        self.assertNotEqual(Tree.objects.get(pk=self.tree.pk).canopy_height, 110)

class InstanceValidationTest(TestCase):

    def setUp(self):

        global_role = Role(name='global', rep_thresh=0)
        global_role.save()

        p = Point(-8515941.0, 4953519.0)
        self.instance1 = Instance(name='i1',geo_rev=0,center=p,default_role=global_role)
        self.instance1.save()

        self.instance2 = Instance(name='i2',geo_rev=0,center=p,default_role=global_role)
        self.instance2.save()

    def test_invalid_instance_returns_404(self):
        response = self.client.get('/%s/' % self.instance1.pk)
        self.assertEqual(response.status_code, 200)

        response = self.client.get('/1000/')
        self.assertEqual(response.status_code, 404)

class ScopeModelTest(TestCase):

    def setUp(self):
        p1 = Point(-8515222.0, 4953200.0)
        p2 = Point(-7515222.0, 3953200.0)

        self.instance1, self.user = make_instance_and_basic_user()
        self.global_role = self.instance1.default_role

        self.instance2 = Instance(name='i2',geo_rev=1,center=p2,default_role=self.global_role)
        self.instance2.save()

        for i in [self.instance1, self.instance2]:
            FieldPermission(model_name='Plot',field_name='geom',
                            permission_level=FieldPermission.WRITE_DIRECTLY,
                            role=self.global_role,
                            instance=i).save()
            FieldPermission(model_name='Tree',field_name='plot',
                            permission_level=FieldPermission.WRITE_DIRECTLY,
                            role=self.global_role,
                            instance=i).save()

        self.plot1 = Plot(geom=p1, instance=self.instance1, created_by=self.user)
        self.plot1.save_with_user(self.user)
        self.plot2 = Plot(geom=p2, instance=self.instance2, created_by=self.user)
        self.plot2.save_with_user(self.user)

        tree_combos = itertools.product(
            [self.plot1, self.plot2],
            [self.instance1, self.instance2],
            [True, False],
            [self.user])

        for tc in tree_combos:
            plot, instance, readonly, created_by = tc
            t = Tree(plot=plot, instance=instance, readonly=readonly, created_by=created_by)
            t.save_with_user(self.user)

    def test_scope_model_method(self):
        all_trees = Tree.objects.all()
        orm_instance_1_trees = list(all_trees.filter(instance=self.instance1))
        orm_instance_2_trees = list(all_trees.filter(instance=self.instance2))

        method_instance_1_trees = list(self.instance1.scope_model(Tree))
        method_instance_2_trees = list(self.instance2.scope_model(Tree))

        # Test that it returns the same as using the ORM
        self.assertEquals(orm_instance_1_trees, method_instance_1_trees)
        self.assertEquals(orm_instance_2_trees, method_instance_2_trees)

        # Test that it didn't grab all trees
        self.assertNotEquals(list(all_trees), method_instance_1_trees)
        self.assertNotEquals(list(all_trees), method_instance_2_trees)

        self.assertRaises(FieldError, (lambda: self.instance1.scope_model(Species)))

class AuditTest(TestCase):

    def setUp(self):

        self.instance = make_instance()
        self.user1 = make_basic_user(self.instance, 'charles')
        self.user2 = make_basic_user(self.instance, 'amy')

        permissions = (
            ('Plot', 'geom', FieldPermission.WRITE_DIRECTLY),
            ('Tree', 'plot', FieldPermission.WRITE_DIRECTLY),
            ('Tree', 'species', FieldPermission.WRITE_DIRECTLY),
            ('Tree', 'import_event', FieldPermission.WRITE_DIRECTLY),
            ('Tree', 'readonly', FieldPermission.WRITE_DIRECTLY),
            ('Tree', 'diameter', FieldPermission.WRITE_DIRECTLY),
            ('Tree', 'height', FieldPermission.WRITE_DIRECTLY),
            ('Tree', 'canopy_height', FieldPermission.WRITE_DIRECTLY),
            ('Tree', 'date_planted', FieldPermission.WRITE_DIRECTLY),
            ('Tree', 'date_removed', FieldPermission.WRITE_DIRECTLY))

        self.user1.roles.add(_make_loaded_role(self.instance, "custom1", 3, permissions))
        self.user2.roles.add(_make_loaded_role(self.instance, "custom2", 3, permissions))

    def assertAuditsEqual(self, exps, acts):
        self.assertEqual(len(exps), len(acts))

        exps = list(exps)
        for act in acts:
            act = act.dict()
            act['created'] = None

            if act in exps:
                exps.remove(act)
            else:
                raise AssertionError('Missing audit record for %s' % act)

    def make_audit(self, pk, field, old, new, action=Audit.Type.Insert, user=None, model=u'Tree'):
        if field:
            field = unicode(field)
        if old:
            old = unicode(old)
        if new:
            new = unicode(new)

        user = user or self.user1

        return { 'model': model,
                 'model_id': pk,
                 'instance_id': self.instance.pk,
                 'field': field,
                 'previous_value': old,
                 'current_value': new,
                 'user_id': user.pk,
                 'action': action,
                 'requires_auth': False,
                 'ref_id': None,
                 'created': None }

    def test_cant_use_regular_methods(self):
        p = Point(-8515222.0, 4953200.0)
        plot = Plot(geom=p, instance=self.instance, created_by=self.user1)
        self.assertRaises(UserTrackingException, plot.save)
        self.assertRaises(UserTrackingException, plot.delete)

        tree = Tree()
        self.assertRaises(UserTrackingException, tree.save)
        self.assertRaises(UserTrackingException, tree.delete)

    def test_basic_audit(self):
        p = Point(-8515222.0, 4953200.0)
        plot = Plot(geom=p, instance=self.instance, created_by=self.user1)
        plot.save_with_user(self.user1)

        self.assertAuditsEqual([
            self.make_audit(plot.pk, 'id', None, str(plot.pk), model='Plot'),
            self.make_audit(plot.pk, 'instance', None, plot.instance.pk , model='Plot'),
            self.make_audit(plot.pk, 'readonly', None, 'False' , model='Plot'),
            self.make_audit(plot.pk, 'geom', None, str(plot.geom), model='Plot'),
            self.make_audit(plot.pk, 'created_by', None, self.user1.pk, model='Plot')],
                               plot.audits())

        t = Tree(plot=plot, instance=self.instance, readonly=True, created_by=self.user1)

        t.save_with_user(self.user1)

        expected_audits = [
            self.make_audit(t.pk, 'id', None, str(t.pk)),
            self.make_audit(t.pk, 'instance', None, t.instance.pk),
            self.make_audit(t.pk, 'readonly', None, True),
            self.make_audit(t.pk, 'created_by', None, self.user1.pk),
            self.make_audit(t.pk, 'plot', None, plot.pk)]


        self.assertAuditsEqual(expected_audits, t.audits())

        t.readonly = False
        t.save_with_user(self.user2)

        expected_audits.insert(
            0, self.make_audit(t.pk, 'readonly', 'True', 'False',
                               action=Audit.Type.Update, user=self.user2))

        self.assertAuditsEqual(expected_audits, t.audits())

        old_pk = t.pk
        t.delete_with_user(self.user1)

        expected_audits.insert(
            0, self.make_audit(old_pk, None, None, None,
                               action=Audit.Type.Delete, user=self.user1))

        self.assertAuditsEqual(
            expected_audits,
            Audit.audits_for_model('Tree', self.instance, old_pk))

class PendingTest(TestCase):
    def setUp(self):
        self.instance = make_instance()
        self.system_user = make_system_user()
        self.system_user.roles.add(make_commander_role(self.instance))

        self.direct_user = make_basic_user(self.instance, "user write direct")
        self.direct_user.roles.add(make_officer_role(self.instance))

        self.pending_user = make_basic_user(self.instance, "user pdg")
        self.pending_user.roles.add(make_apprentice_role(self.instance))

        self.observer_user = make_basic_user(self.instance, "user obs")
        self.observer_user.roles.add(make_observer_role(self.instance))

        self.p1 = Point(-7615441.0, 5953519.0)
        self.plot = Plot(geom=self.p1, instance=self.instance,
                         created_by=self.system_user)
        self.plot.save_with_user(self.direct_user)

    def test_reject(self):
        # Setup
        readonly_orig = self.plot.readonly
        readonly_new = not readonly_orig

        self.plot.readonly = readonly_new
        self.plot.save_with_user(self.pending_user)

        # Generated a single audit
        audit = Audit.objects.filter(requires_auth=True)[0]

        # Should match the model
        self.assertTrue(audit.requires_auth)
        self.assertEqual(audit.model_id, self.plot.pk)

        # Users who don't have direct field access can't reject
        # the edit
        self.assertRaises(AuthorizeException,
                          approve_or_reject_audit_and_apply,
                          audit, self.observer_user, approved=False)

        # User with write access can reject the change
        approve_or_reject_audit_and_apply(
            audit, self.direct_user, approved=False)

        # Reload from DB
        audit = Audit.objects.get(pk=audit.pk)

        # Audit should be marked as processed
        self.assertIsNotNone(audit.ref_id)

        # Ref'd audit should note rejection
        refaudit = Audit.objects.get(pk=audit.ref_id.pk)
        self.assertEqual(refaudit.user, self.direct_user)
        self.assertEqual(refaudit.action, Audit.Type.PendingReject)

        # The object shouldn't have changed
        self.assertEqual(Plot.objects.get(pk=self.plot.pk).readonly,
                         readonly_orig)

        ohash = Plot.objects.get(pk=self.plot.pk).hash

        # Can't reject a pending edit twice
        self.assertRaises(Exception,
                          approve_or_reject_audit_and_apply,
                          audit, self.direct_user, approved=False)

        # Can't approve a pending edit once rejected
        self.assertRaises(Exception,
                          approve_or_reject_audit_and_apply,
                          audit, self.direct_user, approved=False)

        # Nothing was changed, no audits were added
        self.assertEqual(ohash,
                         Plot.objects.get(pk=self.plot.pk).hash)


    def test_accept(self):
        # Setup
        readonly_orig = self.plot.readonly
        readonly_new = not readonly_orig

        self.plot.readonly = readonly_new
        self.plot.save_with_user(self.pending_user)

        # Generated a single audit
        audit = Audit.objects.filter(requires_auth=True)[0]

        # Should match the model
        self.assertTrue(audit.requires_auth)
        self.assertEqual(audit.model_id, self.plot.pk)

        # Users who don't have direct field access can't accept
        # the edit
        self.assertRaises(AuthorizeException,
                          approve_or_reject_audit_and_apply,
                          audit, self.observer_user, approved=True)

        # User with write access can apply the change
        approve_or_reject_audit_and_apply(
            audit, self.direct_user, approved=True)

        # Reload from DB
        audit = Audit.objects.get(pk=audit.pk)

        # Audit should be marked as processed
        self.assertIsNotNone(audit.ref_id)

        # Ref'd audit should note approval
        refaudit = Audit.objects.get(pk=audit.ref_id.pk)
        self.assertEqual(refaudit.user, self.direct_user)
        self.assertEqual(refaudit.action, Audit.Type.PendingApprove)

        # The object should be updated
        self.assertEqual(Plot.objects.get(pk=self.plot.pk).readonly,
                         readonly_new)

        ohash = Plot.objects.get(pk=self.plot.pk).hash

        # Can't approve a pending edit twice
        self.assertRaises(Exception,
                          approve_or_reject_audit_and_apply,
                          audit, self.direct_user, approved=True)

        # Can't reject a pending edit once approved
        self.assertRaises(Exception,
                          approve_or_reject_audit_and_apply,
                          audit, self.direct_user, approved=False)

        # Nothing was changed, no audits were added
        self.assertEqual(ohash,
                         Plot.objects.get(pk=self.plot.pk).hash)



class ReputationTest(TestCase):
    def setUp(self):
        self.instance = make_instance()

        self.system_user = make_system_user()
        self.system_user.roles.add(make_commander_role(self.instance))

        self.privileged_user = make_basic_user(self.instance, "user1")
        self.privileged_user.roles.add(make_officer_role(self.instance))

        self.unprivileged_user = make_basic_user(self.instance, "user2")
        self.unprivileged_user.roles.add(make_apprentice_role(self.instance))

        self.p1 = Point(-7615441.0, 5953519.0)
        self.plot = Plot(geom=self.p1, instance=self.instance, created_by=self.system_user)
        self.plot.save_with_user(self.system_user)

        rm = ReputationMetric(instance=self.instance, model_name='Tree',
                              action=Audit.Type.Insert, direct_write_score=2,
                              approval_score=20, denial_score=5)
        rm.save()

    def test_reputations_increase_for_direct_writes(self):
        self.assertEqual(self.privileged_user.reputation, 0)
        t = Tree(plot=self.plot, instance=self.instance, readonly=True, created_by=self.privileged_user)
        t.save_with_user(self.privileged_user)
        self.assertGreater(self.privileged_user.reputation, 0)

class BoundaryViewTest(TestCase):

    def _make_simple_boundary(self, name, n=1):
        b = Boundary()
        b.geom = MultiPolygon(make_simple_polygon(n))
        b.name = name
        b.category = "Unknown"
        b.sort_order = 1
        b.save()
        return b

    def setUp(self):
        self.factory = RequestFactory()

    def test_boundary_to_geojson_view(self):
        boundary = self._make_simple_boundary("Hello, World", 1)
        request = self.factory.get("/hello/world")
        response = boundary_to_geojson(request, boundary.id)
        self.assertEqual(response.content, boundary.geom.geojson)

    def test_autocomplete_view(self):
        test_instance = make_instance()
        test_boundaries = [
            'alabama',
            'arkansas',
            'far',
            'farquaad\'s castle',
            'farther',
            'farthest',
            'ferenginar',
            'romulan star empire',
        ]
        for i, v in enumerate(test_boundaries):
            test_instance.boundaries.add(self._make_simple_boundary(v, i))
            test_instance.save()

        # make a boundary that is not tied to this
        # instance, should not be in the search
        # results
        self._make_simple_boundary("fargo", 1)

        request = self.factory.get("hello/world", {'q': 'fa'})
        response = boundary_autocomplete(request, make_instance().id)
        response_boundaries = json.loads(response.content)

        self.assertEqual(response_boundaries, test_boundaries[2:6])


class RecentEditsViewTest(TestCase):
    def setUp(self):
        self.instance = make_instance()

        self.system_user = make_system_user()
        self.system_user.roles.add(make_commander_role(self.instance))

        self.officer = User(username="officer")
        self.officer.save_with_user(self.system_user)
        self.officer.roles.add(make_officer_role(self.instance))

        self.pending_user = make_basic_user(self.instance, "user pdg")
        self.pending_user.roles.add(make_apprentice_role(self.instance))

        self.p1 = Point(-7615441.0, 5953519.0)
        self.factory = RequestFactory()

        self.plot = Plot(geom=self.p1, instance=self.instance, created_by=self.system_user)
        self.plot.save_with_user(self.system_user)

        self.tree = Tree(plot=self.plot, instance=self.instance, created_by=self.officer)
        self.tree.save_with_user(self.officer)

        self.tree.diameter = 4
        self.tree.save_with_user(self.officer)

        self.tree.diameter = 5
        self.tree.save_with_user(self.officer)

        self.plot.width = 9
        self.plot.save_with_user(self.system_user)

        self.plot_delta = {
            "model": "Plot",
            "model_id": self.plot.pk,
            "ref_id": None,
            "action": Audit.Type.Update,
            "previous_value": None,
            "current_value": "9",
            "requires_auth": False,
            "user_id": self.system_user.pk,
            "instance_id": self.instance.pk,
            "field": "width"
        }

        self.next_plot_delta = self.plot_delta.copy()
        self.next_plot_delta["current_value"] = "44"
        self.next_plot_delta["previous_value"] = "9"

        self.plot.width = 44
        self.plot.save_with_user(self.system_user)


    def check_audits(self, url, dicts):
        req = self.factory.get(url)
        returns = json.loads(audits(req, self.instance.pk).content)

        self.assertEqual(len(dicts), len(returns))

        for expected, generated in zip(dicts, returns):
            for k,v in expected.iteritems():
                self.assertEqual(v, generated[k])

    def test_multiple_deltas(self):
        self.check_audits('/blah/?page_size=2', [self.next_plot_delta, self.plot_delta])

    def test_paging(self):
        self.check_audits('/blah/?page_size=1&page=1', [self.plot_delta])

    def test_model_filtering_errors(self):
        self.assertRaises(Exception,
                          self.check_audits,
                          "/blah/?model_id=%s&page=0&page_size=1" %\
                          self.tree.pk, [])

        self.assertRaises(Exception,
                          self.check_audits,
                          "/blah/?model_id=%s&models=Tree,Plot&page=0&page_size=1" % \
                          self.tree.pk, [])

        self.assertRaises(Exception,
                          self.check_audits,
                          "/blah/?models=User&page=0&page_size=1", [])

    def test_model_filtering(self):

        specific_tree_delta = {
            "model": "Tree",
            "model_id": self.tree.pk,
            "action": Audit.Type.Update,
            "user_id": self.officer.pk,
        }

        generic_tree_delta = {
            "model": "Tree"
        }

        generic_plot_delta = {
            "model": "Plot"
        }

        self.check_audits(
            "/blah/?model_id=%s&models=Tree&page=0&page_size=1" % self.tree.pk,
            [specific_tree_delta])

        self.check_audits(
            "/blah/?model_id=%s&models=Plot&page=0&page_size=1" % self.plot.pk,
            [self.next_plot_delta])

        self.check_audits(
            "/blah/?models=Plot,Tree&page=0&page_size=3",
            [generic_plot_delta, generic_plot_delta, generic_tree_delta])

        self.check_audits(
            "/blah/?models=Plot&page=0&page_size=5",
            [generic_plot_delta]*5)

        self.check_audits(
            "/blah/?models=Tree&page=0&page_size=5",
            [generic_tree_delta]*5)

    def test_user_filtering(self):

        generic_officer_delta = {
            "user_id": self.officer.pk
        }

        generic_systemuser_delta = {
            "user_id": self.system_user.pk
        }

        self.check_audits(
            "/blah/?user=%s&page_size=3" % self.officer.pk,
            [generic_officer_delta]*3)

        self.check_audits(
            "/blah/?user=%s&page_size=3" % self.system_user.pk,
            [generic_systemuser_delta]*3)


    def test_pending_filtering(self):
        self.plot.width = 22
        self.plot.save_with_user(self.pending_user)

        pending_plot_delta = {
            "model": "Plot",
            "model_id": self.plot.pk,
            "ref_id": None,
            "action": Audit.Type.Update,
            "previous_value": "44",
            "current_value": "22",
            "requires_auth": True,
            "user_id": self.pending_user.pk,
            "instance_id": self.instance.pk,
            "field": "width"
        }

        approve_delta = {
            "action": Audit.Type.PendingApprove,
            "user_id": self.system_user.pk,
            "instance_id": self.instance.pk,
        }

        self.check_audits(
            "/blah/?page_size=2&include_pending=true",
            [pending_plot_delta, self.next_plot_delta])

        self.check_audits(
            "/blah/?page_size=2&include_pending=false",
            [self.next_plot_delta, self.plot_delta])

        a = approve_or_reject_audit_and_apply(
            Audit.objects.all().order_by("-created")[0],
            self.system_user, approved=True)

        pending_plot_delta["ref_id"] = a.pk

        self.check_audits(
            "/blah/?page_size=4&include_pending=false",
            [approve_delta, pending_plot_delta, self.next_plot_delta, self.plot_delta])
