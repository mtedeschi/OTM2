# -*- coding: utf-8 -*-
from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division

from optparse import make_option
import random
import math

from django.contrib.gis.geos import Point, MultiPolygon, Polygon

from treemap.models import Plot, Tree, Species
from treemap.management.util import InstanceDataCommand

from map_features.models import RainGardenLA


class Command(InstanceDataCommand):

    option_list = InstanceDataCommand.option_list + (
        make_option('-r', '--radius',
                    action='store',
                    type='int',
                    dest='radius',
                    default=5000,
                    help='Number of meters from the center'),
        make_option('-n', '--number-of-trees',
                    action='store',
                    type='int',
                    dest='n',
                    default=0,
                    help='Number of trees to create'),
        make_option('-N', '--number-of-resources',
                    action='store',
                    type='int',
                    dest='nresources',
                    default=0,
                    help='Number of resources to create'),
        make_option('-p', '--prob-of-tree',
                    action='store',
                    type='int',
                    dest='ptree',
                    default=50,
                    help=('Probability that a given plot will '
                          'have a tree (0-100)')),
        make_option('-s', '--prob-of-species',
                    action='store',
                    type='int',
                    dest='pspecies',
                    default=50,
                    help=('Probability that a given tree will '
                          'have a species (0-100)')),
        make_option('-D', '--prob-of-diameter',
                    action='store',
                    type='int',
                    dest='pdiameter',
                    default=10,
                    help=('Probability that a given tree will '
                          'have a diameter (0-100)')))

    def handle(self, *args, **options):
        """ Create some seed data """
        instance, user = self.setup_env(*args, **options)

        species_qs = instance.scope_model(Species)

        n = options['n']
        nresources = options['nresources']
        if n > 0:
            self.stdout.write("Will create %s plots" % n)
        if nresources > 0:
            self.stdout.write("Will create %s resources" % nresources)

        get_prob = lambda option: float(min(100, max(0, option))) / 100.0
        tree_prob = get_prob(options['ptree'])
        species_prob = get_prob(options['pspecies'])
        diameter_prob = get_prob(options['pdiameter'])
        max_radius = options['radius']

        center_x = instance.center.x
        center_y = instance.center.y

        def random_point():
            radius = random.gauss(0.0, max_radius)
            theta = random.random() * 2.0 * math.pi
            x = math.cos(theta) * radius + center_x
            y = math.sin(theta) * radius + center_y
            return Point(x, y)

        ct = 0
        cp = 0
        cr = 0
        for i in xrange(0, n):
            mktree = random.random() < tree_prob

            plot = Plot(instance=instance,
                        geom=random_point())

            plot.save_with_user(user)
            cp += 1

            if mktree:
                add_species = random.random() < species_prob
                if add_species:
                    species = random.choice(species_qs)
                else:
                    species = None

                add_diameter = random.random() < diameter_prob
                if add_diameter:
                    diameter = 2 + random.random() * 18
                else:
                    diameter = None

                tree = Tree(plot=plot,
                            species=species,
                            diameter=diameter,
                            instance=instance)
                tree.save_with_user(user)
                ct += 1

        for i in xrange(0, nresources):
            box = MultiPolygon(Polygon(
                ((0, 0), (100, 0), (100, 100), (0, 100), (0, 0))))
            garden = RainGardenLA(instance=instance, geom=random_point(),
                                  roof_geometry=box)

            garden.save_with_user(user)
            cr += 1

        if n > 0:
            self.stdout.write("Created %s trees and %s plots" % (ct, cp))
        if nresources > 0:
            self.stdout.write("Created %s rain gardens" % cr)
