import abc
from openpathsampling.netcdfplus import ObjectStore, LoaderProxy, StorableObject
from openpathsampling.netcdfplus.objects import UUIDDict, IndexedObjectStore
import openpathsampling.engines as peng

from collections import OrderedDict
from uuid import UUID

from openpathsampling.netcdfplus import NetCDFPlus, ObjectStore, WeakLRUCache, LRUChunkLoadingCache

from openpathsampling.engines import BaseSnapshot



# =============================================================================================
# ABSTRACT BASE CLASS FOR SNAPSHOTS
# =============================================================================================

class UUIDReversalDict(UUIDDict):
    @staticmethod
    def rev_id(obj):
        return StorableObject.ruuid(UUIDReversalDict.id(obj))

    def __setitem__(self, key, value):
        OrderedDict.__setitem__(self, self.id(key), value)
        OrderedDict.__setitem__(self, self.rev_id(key), value ^ 1)

    def __delitem__(self, key):
        OrderedDict.__delitem__(self, self.id(key))
        OrderedDict.__delitem__(self, self.rev_id(key))


class BaseSnapshotStore(ObjectStore):
    """
    An ObjectStore for Snapshots in netCDF files.
    """

    __metaclass__ = abc.ABCMeta

    def __init__(self, snapshot_class):
        """

        Attributes
        ----------
        snapshot_class : openpathsampling.BaseSnapshot
            a snapshot class that this Store is supposed to store

        """
        super(BaseSnapshotStore, self).__init__(peng.BaseSnapshot, json=False)
        self.snapshot_class = snapshot_class
        self._use_lazy_reversed = False
        if hasattr(snapshot_class, '__features__'):
            if '_reversed' in snapshot_class.__features__.lazy:
                self._use_lazy_reversed = True

    def create_uuid_index(self):
        return UUIDReversalDict()

    def __repr__(self):
        return "store.%s[%s(%s)]" % (
            self.prefix, self.snapshot_class.__name__, self.content_class.__name__)

    @staticmethod
    def paired_idx(idx):
        """
        Return the paired index

        Snapshots are stored in pairs (2n, 2n+1) where one is the reversed copy.
        This make storing CVs easier. This function allows to get the paired index
        or the index of snapshot.reversed

        The implementation uses the trick that all you have to do is flip the lowest bit
        that determines even or odd.

        Parameters
        ----------
        idx : int
            the one part of the paired index

        Returns
        -------
        int
            the other part of the paired index
        """
        return idx ^ 1

    def to_dict(self):
        return {
            'snapshot_class': self.snapshot_class
        }

    def _load(self, idx):
        """
        Load a snapshot from the storage.

        Parameters
        ----------
        idx : int
            the integer index of the snapshot to be loaded

        Returns
        -------
        snapshot : :obj:`BaseSnapshot`
            the loaded snapshot instance
        """

        # check if the reversed is in the cache
        try:
            return self.cache[BaseSnapshotStore.paired_idx(idx)].reversed
        except KeyError:
            pass

        # if not load and return it
        st_idx = int(idx / 2)

        obj = self.snapshot_class.__new__(self.snapshot_class)
        self.snapshot_class.init_empty(obj)

        self._get(st_idx, obj)
        if idx & 1:
            obj = obj.reversed

        # obj._reversed = LoaderProxy(self, BaseSnapshotStore.paired_idx(idx))
        return obj

    @abc.abstractmethod
    def _set(self, idx, snapshot):
        pass

    @abc.abstractmethod
    def _get(self, idx, snapshot):
        pass

    def _set_id(self, idx, obj):
        if self.reference_by_uuid:
            self.vars['uuid'][int(idx / 2)] = obj.__uuid__

    def _get_id(self, idx, obj):
        if self.reference_by_uuid:
            uuid = self.vars['uuid'][int(idx / 2)]
            if idx & 1:
                uuid = StorableObject.ruuid(uuid)

            obj.__uuid__ = uuid

    def load_indices(self):
        if self.reference_by_uuid:
            for idx, uuid in enumerate(self.vars['uuid'][:]):
                self.index[uuid] = idx * 2

    def _save(self, snapshot, idx):
        """
        Add the current state of the snapshot in the database.

        Parameters
        ----------
        snapshot :class:`openpathsampling.snapshots.AbstractSnapshot`
            the snapshot to be saved
        idx : int or None
            if idx is not None the index will be used for saving in the storage.
            This might overwrite already existing trajectories!

        Notes
        -----
        This also saves all contained frames in the snapshot if not done yet.
        A single Snapshot object can only be saved once!
        """

        st_idx = int(idx / 2)

        if snapshot._reversed is not None:
            if not self.reference_by_uuid and snapshot._reversed in self.index:
                # seems we have already stored this snapshot but didn't know about it
                raise RuntimeWarning('This should never happen! Please report a bug!')
            else:
                # mark reversed as stored
                self.index[snapshot._reversed] = BaseSnapshotStore.paired_idx(idx)

        self._set(st_idx, snapshot)

        if snapshot._reversed is not None:
            # mark reversed as stored
            self.index[snapshot._reversed] = BaseSnapshotStore.paired_idx(idx)

    def save(self, obj, idx=None):
        if self.reference_by_uuid:
            ruuid = str(UUID(int=int(obj.__uuid__)))

            if ruuid in self.index:
                # has been saved so quit and do nothing
                return obj.__uuid__

        if obj._reversed is not None:
            if not self.reference_by_uuid and obj._reversed in self.index:
                # the reversed copy has been saved so quit and return the paired idx
                self.index[obj] = BaseSnapshotStore.paired_idx(self.index[obj._reversed])

        return super(BaseSnapshotStore, self).save(obj, idx)

    def all(self):
        if self.reference_by_uuid:
            return peng.Trajectory(map(self.proxy, self.index))
        else:
            return peng.Trajectory(map(self.proxy, range(len(self))))

    def __len__(self):
        return 2 * super(BaseSnapshotStore, self).__len__()

    def duplicate(self, snapshot):
        """
        Store a duplicate of the snapshot as new

        Parameters
        ----------
        snapshot :class:`openpathsampling.snapshots.AbstractSnapshot`

        Returns
        -------
        int
            the index used for storing it in the store. This is the save as used by
            save.

        Notes
        -----
        This will circumvent the caching and indexing completely. This would be equivalent
        of creating a copy of the current snapshot and store this one and throw the copy
        away, leaving the given snapshot untouched. This allows you to treat the snapshot
        as mutual.

        The use becomes more obvious when applying to storing trajectories. The only way
        to make use of this feature is using the returned `idx`

        >>> idx = store.duplicate(snap)
        >>> loaded = store[idx]  # return a duplicated as new object
        >>> proxy = paths.LoaderProxy(store, idx) # use the duplicate without loading

        """
        idx = self.free()
        st_idx = int(idx / 2)
        self._set(st_idx, snapshot)

        return idx

    def idx(self, obj):
        try:
            return self.index[obj]
        except KeyError:
            pass

        try:
            return BaseSnapshotStore.paired_idx(self.index[obj.reversed])
        except KeyError:
            return None


class BaseSnapshotIndexedStore(IndexedObjectStore):
    """
    An ObjectStore for Snapshots in netCDF files.
    """

    __metaclass__ = abc.ABCMeta

    def __init__(self, descriptor):
        """

        Attributes
        ----------
        snapshot_class : openpathsampling.BaseSnapshot
            a snapshot class that this Store is supposed to store

        """

        # Using a store with None as type will not interfere with the main snapshotstore
        super(BaseSnapshotIndexedStore, self).__init__(None, json=False)
        self.descriptor = descriptor
        self._dimensions = descriptor.dimensions
        self._cls = self.descriptor.snapshot_class

        # self._use_lazy_reversed = False
        # if hasattr(snapshot_class, '__features__'):
        #     if '_reversed' in snapshot_class.__features__.lazy:
        #         self._use_lazy_reversed = True

    @property
    def reference_by_uuid(self):
        # This one does explicitly use integer indices
        return False

    @property
    def snapshot_class(self):
        return self._cls

    @property
    def dimensions(self):
        return self.descriptor['dimensions']

    def __repr__(self):
        return "store.%s[%s(%s)]" % (
            self.prefix, self._cls.__name__, 'BaseSnapshot')

    def to_dict(self):
        return {
            'descriptor': self.descriptor,
        }

    def _get_id(self, idx, obj):
        if self.reference_by_uuid:
            uuid = self.vars['uuid'][int(idx / 2)]
            if idx & 1:
                uuid = StorableObject.ruuid(uuid)

            obj.__uuid__ = uuid

    def load(self, idx):
        pos = idx / 2

        # we want to load by uuid and it was not in cache.
        if pos in self.index:
            n_idx = self.index[pos]
        else:
            raise KeyError(idx)

        if n_idx < 0:
            return None

        # if it is in the cache, return it
        try:
            obj = self.cache[n_idx]
            if idx & 1:
                obj = obj.reversed

            return obj

        except KeyError:
            pass

        obj = self._load(n_idx)
        self.cache[n_idx] = obj

        if idx & 1:
            obj = obj.reversed

        return obj

    def _load(self, idx):
        """
        Load a snapshot from the storage.

        Parameters
        ----------
        idx : int
            the integer index of the snapshot to be loaded

        Returns
        -------
        snapshot : :obj:`BaseSnapshot`
            the loaded snapshot instance
        """

        # if not load and return it
        st_idx = int(idx)

        obj = self._cls.__new__(self._cls)
        self._cls.init_empty(obj)
        self._get(st_idx, obj)
        return obj

    @abc.abstractmethod
    def _set(self, idx, snapshot):
        pass

    @abc.abstractmethod
    def _get(self, idx, snapshot):
        pass

    def _set_id(self, idx, obj):
        if self.reference_by_uuid:
            self.vars['uuid'][int(idx / 2)] = obj.__uuid__

    def _get_id(self, idx, obj):
        if self.reference_by_uuid:
            uuid = self.vars['uuid'][int(idx / 2)]
            if idx & 1:
                uuid = StorableObject.ruuid(uuid)

            obj.__uuid__ = uuid

    def load_indices(self):
        if self.reference_by_uuid:
            for pos, idx in enumerate(self.vars['index'][:]):
                self.index[idx] = pos

    def _save(self, snapshot, idx):
        """
        Add the current state of the snapshot in the database.

        Parameters
        ----------
        snapshot :class:`openpathsampling.snapshots.AbstractSnapshot`
            the snapshot to be saved
        idx : int or None
            if idx is not None the index will be used for saving in the storage.
            This might overwrite already existing trajectories!

        Notes
        -----
        This also saves all contained frames in the snapshot if not done yet.
        A single Snapshot object can only be saved once!
        """

        self._set(idx, snapshot)

    def all(self):
        return peng.Trajectory(map(self.proxy, range(len(self))))

    def duplicate(self, snapshot):
        """
        Store a duplicate of the snapshot as new

        Parameters
        ----------
        snapshot :class:`openpathsampling.snapshots.AbstractSnapshot`

        Returns
        -------
        int
            the index used for storing it in the store. This is the save as used by
            save.

        Notes
        -----
        This will circumvent the caching and indexing completely. This would be equivalent
        of creating a copy of the current snapshot and store this one and throw the copy
        away, leaving the given snapshot untouched. This allows you to treat the snapshot
        as mutual.

        The use becomes more obvious when applying to storing trajectories. The only way
        to make use of this feature is using the returned `idx`

        >>> idx = store.duplicate(snap)
        >>> loaded = store[idx]  # return a duplicated as new object
        >>> proxy = paths.LoaderProxy(store, idx) # use the duplicate without loading

        """
        idx = self.free()
        st_idx = int(idx)
        self._set(st_idx, snapshot)

        return idx

    def idx(self, obj):
        return self.index[obj]

# =============================================================================================
# FEATURE BASED SINGLE CLASS FOR ALL SNAPSHOT TYPES
# =============================================================================================

class FeatureSnapshotStore(BaseSnapshotStore):
    """
    An ObjectStore for Snapshots in netCDF files.
    """

    def __init__(self, snapshot_class):
        super(FeatureSnapshotStore, self).__init__(snapshot_class)

    @property
    def classes(self):
        return self.snapshot_class.__features__.classes

    @property
    def storables(self):
        return self.snapshot_class.__features__.storables

    def _set(self, idx, snapshot):
        [self.write(attr, idx, snapshot) for attr in self.storables]

    def _get(self, idx, snapshot):
        [setattr(snapshot, attr, self.vars[attr][idx]) for attr in self.storables]

    def initialize(self):
        super(FeatureSnapshotStore, self).initialize()

        for feature in self.classes:
            if hasattr(feature, 'netcdfplus_init'):
                feature.netcdfplus_init(self)


class FeatureSnapshotIndexedStore(BaseSnapshotIndexedStore):
    """
    An ObjectStore for Snapshots in netCDF files.
    """

    def __init__(self, descriptor):
        super(FeatureSnapshotIndexedStore, self).__init__(
            descriptor
    )

    @property
    def classes(self):
        return self.snapshot_class.__features__.classes

    @property
    def storables(self):
        return self.snapshot_class.__features__.storables

    def _set(self, idx, snapshot):
        [self.write(attr, idx, snapshot) for attr in self.storables]

    def _get(self, idx, snapshot):
        [setattr(snapshot, attr, self.vars[attr][idx]) for attr in self.storables]

    def initialize(self):
        super(FeatureSnapshotIndexedStore, self).initialize()

        for dim, size in self._dimensions.iteritems():
            self.storage.create_dimension(self.prefix + dim, size)

        for feature in self.classes:
            if hasattr(feature, 'netcdfplus_init'):
                feature.netcdfplus_init(self)

        self.storage.sync()


class SnapshotWrapperStore(ObjectStore):
    """
    A Store to store arbitrary snapshots
    """
    def __init__(self):
        super(SnapshotWrapperStore, self).__init__(peng.BaseSnapshot, json=False)

        self.type_list = {}
        self.store_snapshot_list = []
        self.store_cv_list = []
        self.cv_list = {}

        self._treat_missing_snapshot_type = 'create'


    @property
    def treat_missing_snapshot_type(self):
        return self._treat_missing_snapshot_type

    @treat_missing_snapshot_type.setter
    def treat_missing_snapshot_type(self, value):
        allowed = ['create', 'ignore', 'fail']
        if value not in allowed:
            raise ValueError('Only one of %s choices allowed.' % allowed)

        self._treat_missing_snapshot_type = value

    def _load(self, idx):
        store_idx = self.vars['store'][idx / 2]

        if store_idx < 0:
            raise ValueError('IDX "' + idx + '" not found in storage')
        else:
            store = self.store_snapshot_list[store_idx]
            return store[idx]

    def __len__(self):
        return len(self.storage.dimensions[self.prefix]) * 2

    def initialize(self):
        super(SnapshotWrapperStore, self).initialize()

        self.create_variable('store', 'index')

        self.storage.create_dimension('snapshottype')
        self.storage.create_dimension('cvcache')

        self.storage.create_variable('snapshottype', 'obj.stores', 'snapshottype')
        self.storage.create_variable('cvcache', 'obj.stores', 'cvcache')

    def add_type(self, descriptor):
        if isinstance(descriptor, peng.BaseSnapshot):
            template = descriptor
            descriptor = descriptor.engine.descriptor
        else:
            template = None

        if descriptor in self.type_list:
            return self.type_list[descriptor]

        store = FeatureSnapshotIndexedStore(descriptor)

        store_idx = int(len(self.storage.dimensions['snapshottype']))
        store_name = 'snapshot' + str(store_idx)
        self.storage.register_store(store_name, store, False)

        # this will tell the store to add its own prefix for dimension names
        store.set_dimension_prefix_store(store)

        store.name = store_name
        self.storage.stores.save(store)

        self.type_list[descriptor] = (store, store_idx)
        self.store_snapshot_list.append(store)
        self.storage.vars['snapshottype'][store_idx] = store

        self.storage.finalize_stores()
        self.storage.update_delegates()

        if template:
            self.save(template)

        return store, store_idx

    @staticmethod
    def _snapshot_store_name(idx):
        return 'snapshot' + str(idx)

    @staticmethod
    def to_descriptor(cls, dims):
        description = {}
        description.update(dims)
        description['class'] = cls

        return description

    def restore(self):
        for idx, store in enumerate(self.storage.vars['snapshottype']):
            self.type_list[store.descriptor] = (store, idx)
            self.store_snapshot_list.append(store)

        for idx, store in enumerate(self.storage.vars['cvcache']):
            self.cv_list[store.cv] = (store, idx)

        self.load_indices()

    def load_indices(self):
        if self.reference_by_uuid:
            for idx, uuid in enumerate(self.vars['uuid'][:]):
                self.index[uuid] = idx * 2

    def save(self, obj, idx=None):
        n_idx = None

        if self.reference_by_uuid:
            if obj in self.index:
                n_idx = self.index[obj]
        else:
            if hasattr(obj, '_idx'):
                if obj._store is self:
                    # is a proxy of a saved object so do nothing
                    return obj._idx
                else:
                    return self.save(obj.__subject__)

            if obj in self.index:
                n_idx = self.index[obj]

            elif obj._reversed is not None:
                # if the object has no reversed present, then the reversed does not
                # exist yet and hence it cannot be in the index, so no checking
                if obj._reversed in self.index:
                    n_idx = self.index[obj._reversed] ^ 1

        if n_idx is not None:
            store_idx = self.variables['store'][n_idx / 2]
            if not store_idx == -1:
                return self.reference(obj)

        if not isinstance(obj, self.content_class):
            raise ValueError(
                'This store can only store object of base type "%s". Given obj is of type "%s". You'
                'might need to use another store.' % (self.content_class, obj.__class__.__name__)
            )

        if n_idx is None:
            n_idx = self.free()

            self._save(obj, n_idx)
            self._set_id(n_idx, obj)
        else:
            self._put_in_store(self.store_snapshot_list[store_idx], store_idx, obj, n_idx)

        self.cache[n_idx] = obj

        return self.reference(obj)

    def _put_in_store(self, store, store_idx, obj, idx):
        store[idx / 2] = obj
        self.vars['store'][idx / 2] = store_idx
        self.index[obj] = idx

    def _save(self, obj, idx):
        try:
            store, store_idx = self.type_list[obj.engine.descriptor]
            self._put_in_store(store, store_idx, obj, idx)
            return store

        except KeyError:
            # Apparently there is no store yet to handle the given type of snapshot
            if self.treat_missing_snapshot_type == 'create':
                # we just create space for it
                store, store_idx = self.add_type(obj.engine.descriptor)
                self._put_in_store(store, store_idx, obj, idx)
                return store

            elif self.treat_missing_snapshot_type == 'ignore':
                # we keep silent about it
                self.vars['store'][idx / 2] = -1
                return None
            else:
                # we fail with cannot store
                raise RuntimeError(
                    (
                        'The store cannot hold snapshots of the given type : '
                        'class "%s" and dimensions %s. Try adding the snapshot type '
                        'using .add_type(snapshot).'
                    ) % (
                        obj.__class__.__name__,
                        obj.engine.descriptor.dimensions
                    )
                )

    def free(self):
        """
        Return the number of the next free index for this store

        Returns
        -------
        index : int
            the number of the next free index in the storage.
            Used to store a new object.
        """


        # start at first free position in the storage
        idx = len(self)

        # and skip also reserved potential stored ones
        while idx in self._free:
            # we need to skip 2 for the reversible pairs instead of one
            idx += 2

        return idx

    def get_uuid_index(self, obj):
        n_idx = None

        if self.reference_by_uuid:
            if obj in self.index:
                n_idx = self.index[obj]
        else:
            if hasattr(obj, '_idx'):
                if obj._store is self:
                    # is a proxy of a saved object so do nothing
                    return obj._idx

            if obj in self.index:
                n_idx = self.index[obj]

            elif obj._reversed is not None:
                if obj._reversed in self.index:
                    n_idx = self.index[obj._reversed] ^ 1

        if n_idx is None:
            # if the obj is not know, add it to the file and index, but
            # store only a reference and not the full object
            # this can later be done using .save(obj)
            n_idx = self.free()
            self.variables['store'][n_idx / 2] = -1
            self.index[obj] = n_idx
            self._set_id(n_idx, obj)

    def add_cv(self, cv, template, chunksize=100):

        if cv in self.cv_list:
            return self.cv_list[cv]

        # determine value type and shape
        params = NetCDFPlus.get_value_parameters(template)
        shape = params['dimensions']

        if shape is None:
            chunksizes = None
        else:
            chunksizes = tuple(params['dimensions'])

        store = SnapshotValueStore(cv)

        # make sure the cv is saved
        # if cv not in self.storage.cvs.index:
        #     self.storage.cvs.save(cv)

        var_name = 'cv' + str(self.storage.cvs.index[cv])

        print var_name

        self.storage.create_store(var_name, store, False)

        print 'No possible'

        # we are not using the .initialize function here since we
        # only have one variable and only here know its shape
        self.storage.create_dimension(store.prefix, 0)

        if shape is not None:
            shape = tuple(list(shape))
            chunksizes = tuple([chunksize] + list(chunksizes))
        else:
            shape = tuple()
            chunksizes = tuple([chunksize])

        # create the variable
        store.create_variable(
            'value',
            var_type=params['var_type'],
            dimensions=shape,
            chunksizes=chunksizes,
            simtk_unit=params['simtk_unit'],
        )

        store.create_variable('index', 'index')
        # self.storage.create_variable_delegate(var_name + '_value')
        # self.storage.create_variable_delegate(var_name + '_index')

        setattr(store, 'value', self.storage.vars[var_name + '_value'])

        store.set_caching(LRUChunkLoadingCache(
            chunksize=chunksize,
            max_chunks=1000,
            variable=store.value
        ))

        store_idx = int(len(self.storage.dimensions['cvcache']))
        self.cv_list[cv] = (store, store_idx)
        self.storage.vars['cvcache'][store_idx] = store

        cv.set_cache_store(store)

        return store, store_idx

    def create_uuid_index(self):
        return UUIDReversalDict()

    def _get_id(self, idx, obj):
        if self.reference_by_uuid:
            uuid = self.vars['uuid'][int(idx / 2)]
            if idx & 1:
                uuid = StorableObject.ruuid(uuid)

            obj.__uuid__ = uuid

    def _set_id(self, idx, obj):
        if self.reference_by_uuid:
            self.vars['uuid'][idx / 2] = obj.__uuid__

    def idx(self, obj):
        """
        Return the index in this store for a given object

        Parameters
        ----------
        obj : :py:class:`openpathsampling.netcdfplus.base.StorableObject`
            the object that can be stored in this store for which its index is
            to be returned

        Returns
        -------
        int or None
            The integer index of the given object or None if it is not stored yet
        """
        try:
            return self.index[obj]
        except:
            try:
                return self.index[obj._reversed] ^ 1
            except:
                raise KeyError(obj)


class SnapshotValueStore(ObjectStore):
    def __init__(self, cv):
        super(SnapshotValueStore, self).__init__(None)
        self.uuid_index = None
        self.cv = cv

    def to_dict(self):
        return {
            'cv': self.cv
        }

    def create_uuid_index(self):
        return dict()

    def register(self, storage, prefix):
        super(SnapshotValueStore, self).register(storage, prefix)
        self.uuid_index = self.storage.snapshots.index

    # =============================================================================
    # LOAD/SAVE DECORATORS FOR CACHE HANDLING
    # =============================================================================

    def load(self, idx):
        """
        Returns an object from the storage.

        Parameters
        ----------
        idx : int
            the integer index of the object to be loaded

        Returns
        -------
        :py:class:`openpathsampling.netcdfplus.base.StorableObject`
            the loaded object
        """

        if self.reference_by_uuid:
            pos = self.uuid_index[idx]
        else:
            pos = idx

        if self.cv.time_reversible:
            pos -= pos % 2

        # we want to load by uuid and it was not in cache.
        if pos in self.index:
            n_idx = self.index[pos]
        else:
            return None

        if n_idx < 0:
            return None

        # if it is in the cache, return it
        try:
            obj = self.cache[n_idx]
            return obj

        except KeyError:
            pass

        obj = self.vars['value'][n_idx]

        self.cache[n_idx] = obj

        return obj

    def __setitem__(self, idx, value):
        """
        Saves an object to the storage.

        Parameters
        ----------
        idx : :py:class:`openpathsampling.engines.BaseSnapshot`
            the object to be stored
        value : anything that can be stored
            this includes storable objects, python numbers, numpy.arrays,
            strings, etc.

        """

        if self.cv.time_reversible:
            if idx in self.index:
                # has been saved so quit and do nothing
                return

            if idx._reversed and idx._reversed in self.index:
                return

            pos = self.uuid_index.get(idx)
            if pos is None:
                return

            pos -= pos % 2

        else:

            if idx in self.index:
                # has been saved so quit and do nothing
                return

            pos = self.uuid_index.get(idx)
            if pos is None:
                return

        n_idx = self.free()
        self.vars['value'][n_idx] = value
        self.vars['index'][n_idx] = pos

        self.index[idx] = n_idx
        self.cache[n_idx] = value

    def sync(self, cv):
        if not self.reference_by_uuid:
            # for uuids this cannot happen
            # necessary if we compute cvs that are not stored
            pass

    def restore(self):
        if self.reference_by_uuid:
            for pos, idx in enumerate(self.vars['index'][:]):
                self.index[idx] = pos
        else:
            for pos, idx in enumerate(self.vars['index'][:]):
                self.index[idx] = pos

    def initialize(self):
        pass

    def __getitem__(self, item):
        """
        Enable numpy style selection of object in the store
        """
        try:
            if isinstance(item, BaseSnapshot):
                return self.load(item)
            elif type(item) is list:
                return [self.load(idx) for idx in item]
            elif item is Ellipsis:
                return self.iterator()
        except KeyError:
            return None

    def get(self, item):
        if item in self.index:
            return self[item]
        else:
            return None
