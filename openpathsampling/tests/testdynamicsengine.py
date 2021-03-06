import openpathsampling as paths

from nose.tools import (assert_equal, assert_not_equal, raises)
from nose.plugins.skip import SkipTest
from test_helpers import make_1d_traj, raises_with_message_like


class StupidEngine(paths.engines.DynamicsEngine):
    _default_options = {'random_option': False}

    @property
    def bad_property(self):
        obj = object()
        return obj.b

    @property
    def property_recovers(self):
        if not hasattr(self, 'attempted'):
            self.attempted = True
            raise AttributeError("Internal error")
        return self.attempted


class testDynamicsEngine(object):
    def setup(self):
        options = {'n_frames_max' : 100, 'random_option' : True}

        snapshot_dimensions = {
            'n_atoms': 1,
            'n_spatial': 1
        }

        descriptor = paths.engines.snapshot.SnapshotDescriptor.construct(
            snapshot_class=paths.engines.toy.Snapshot,
            snapshot_dimensions=snapshot_dimensions
        )
        self.engine = paths.engines.DynamicsEngine(options, descriptor)
        self.stupid = StupidEngine(options, descriptor)

    def test_getattr_from_options(self):
        assert_equal(self.stupid.random_option, True)

    @raises_with_message_like(AttributeError, 
                              "'object' object has no attribute 'b'")
    def test_getattr_property_fails(self):
        self.stupid.bad_property

    @raises_with_message_like(AttributeError, 
                              "Unknown problem occurred in property")
    def test_getattr_property_recovers(self):
        self.stupid.property_recovers

    @raises_with_message_like(AttributeError,
                              "'StupidEngine' has no attribute 'foo'" +  
                              ", nor does its options dictionary")
    def test_getattr_does_not_exist(self):
        self.stupid.foo

    def test_getattr_dimension(self):
        assert(self.engine.n_atoms == 1)
        assert (self.engine.n_spatial == 1)
        assert(self.stupid.n_atoms == 1)
        assert (self.stupid.n_spatial == 1)
