'''
Created on 19.07.2014

@author: Jan-Hendrik Prinz
'''

import numpy as np
import random

from shooting import ShootingPoint
from ensemble import ForwardAppendedTrajectoryEnsemble, BackwardPrependedTrajectoryEnsemble
from ensemble import FullEnsemble
from trajectory import Sample
from wrapper import storable


@storable
class MoveDetails(object):
    '''Details of the move as applied to a given replica

    RENAME: inputs=>initial
            final=>trial

    Attributes
    ----------
    replica : integer
        replica ID to which this trial move would apply
    inputs : list of Sample
        the Samples which were used as inputs to the move
    final : tuple(Tractory, Ensemble)
        the Trajectory and Ensemble which 
    accepted : bool
        whether the attempted move was accepted
    mover : PathMover
        the PathMover which generated this trial

    TODO (or at least to put somewhere):
    rejection_reason : String
        explanation of reasons the path was rejected

    Specific move types may have add several other attributes for each
    MoveDetails object. For example, shooting moves will also include
    information about the shooting point selection, etc.

    '''

    def __init__(self, **kwargs):
        self.inputs=None
        self.final=None
        self.result=None
        self.acceptance=None
        self.success=None
        self.accepted=None
        self.mover=None
        for key, value in kwargs:
            setattr(self, key, value)


class PathMover(object):
    """
    A PathMover is the description of how to generate a new path from an old one.
    
    Notes
    -----
    
    Basically this describes the proposal step for a MC in path space.
    
    We might detach this from the acceptance step?!?!?
    This would mean that a PathMover needs only an old trajectory and gives
    a new one.
    
    For example a ForwardShoot then uses a shooting point selector and runs
    a new trajectory and combine them to get a new one.
    
    After the move has been made, we can retrieve information about the
    move, as well as the new trajectory from the PathMover object
    
    Potential future change: `engine` is not needed for all PathMovers
    (replica exchange, ensemble hopping, path reversal, and moves which
    combine these [state swap] have no need for the engine). Maybe that
    should be moved into only the ensembles that need it? ~~~DWHS

    Also, I agree with the separating trial and acceptance. We might choose
    to use a different acceptance criterion than Metropolis. For example,
    the "waste recycling" approach recently re-discovered by Frenkel (see
    also work by Athenes, Jourdain, and old work by Kalos) might be
    interesting. ~~~DWHS


    Attributes
    ----------
    engine : DynamicsEngine
        the attached engine used to generate new trajectories

    """

    cls = 'pathmover'
    engine = None

    @property
    def identifier(self):
        if hasattr(self, 'json'):
            return self.json
        else:
            return None

    def __init__(self, replicas='all', ensembles=None):
        
        self.name = self.__class__.__name__

        if type(replicas) is int:
            self.replicas = [replicas]
        else:
            self.replicas = replicas

        if type(ensemble) is not list:
            ensembles = [ensembles]
        self.ensembles = ensembles

        self.idx = dict()

    def legal_sample_set(self, globalstate, ensembles=None):
        if self.replicas == 'all':
            reps = globalstate.replica_list()
        else:
            reps = self.replicas
        rep_samples = []
        for rep in reps:
            rep_samples.extend(globalstate.all_from_replica(rep))

        if ensembles is not None:
            ens_samples = []
            if type(ensembles) is not list:
                ensembles = [ensembles]
            for ens in ensembles:
                ens_samples.extend(globalstate.all_from_ensemble(ens))
            legal_samples = list(set(rep_samples) & set(ens_samples))
        else:
            legal_samples = rep_samples

        return legal_samples

    def select_sample(self, globalstate, ensembles=None):
        legal = self.legal_sample_set(globalstate, ensembles)
        return random.choice(legal)

    def move(self, globalstate):
        '''
        Run the generation starting with the initial trajectory specified.

        Parameters
        ----------
        globalstate : GlobalState
            the initial global state
        
        Returns
        -------        
        samples : list of Sample()
            the new samples
        
        Notes
        -----
        After this command additional information can be accessed from this
        object (??? can you explain this, JHP?)
        '''

        return []

    def selection_probability_ratio(self, details=None):
        '''
        Return the proposal probability necessary to correct for an
        asymmetric proposal.
        
        Notes
        -----
        This is effectively the ratio of proposal probabilities for a mover.
        For symmetric proposal this is one. In the case of e.g. Shooters
        this depends on the used ShootingPointSelector and the start and
        final trajectory.
        
        I am not sure if it makes sense that to define it this way, but for
        Shooters this is, what we need for the acceptance step in addition
        to the check if we have a trajectory of
        the target ensemble.

        What about Minus Move and PathReversalMove?
        '''
        return 1.0

class ShootMover(PathMover):
    '''
    A pathmover that implements a general shooting algorithm that generates
    a sample from a specified ensemble 
    '''

    def __init__(self, selector, ensembles=None, replicas='all'):
        super(ShootMover, self, ensembles, replicas).__init__()
        self.selector = selector
        self.length_stopper = PathMover.engine.max_length_stopper

    def selection_probability_ratio(self, details):
        '''
        Return the proposal probability for Shooting Moves. These are given
        by the ratio of partition functions
        '''
        return details.start_point.sum_bias / details.final_point.sum_bias
    
    def _generate(self):
        self.final = self.start
    
    def move(self, globalstate):
        # select a legal sample, use it to determine the trajectory and the
        # ensemble needed for the dynamics
        rep_sample = self.select_sample(globalstate, self.ensembles) 
        trajectory = rep_sample.trajectory
        dynamics_ensemble = rep_sample.ensemble

        details = MoveDetails()
        details.success = False
        details.inputs = [trajectory]
        details.mover = self
        setattr(details, 'start', trajectory)
        setattr(details, 'start_point', self.selector.pick(details.start) )
        setattr(details, 'final', None)
        setattr(details, 'final_point', None)

        self._generate(details)


        details.accepted = dynamics_ensemble(details.final)
        details.result = details.start

        if details.accepted:
            rand = np.random.random()
            print 'Proposal probability', self.selection_probability_ratio(details), '/ random :', rand
            if (rand < self.selection_probability_ratio(details)):
                details.success = True
                details.result = details.final

        path = Sample(trajectory=details.result, mover=self,
                      ensemble=dynamics_ensemble, details=details)

        return path
    
    
class ForwardShootMover(ShootMover):    
    '''
    A pathmover that implements the forward shooting algorithm
    '''
    def _generate(self, details, ensemble):
        shooting_point = details.start_point.index
        print "Shooting forward from frame %d" % shooting_point
        
        # Run until one of the stoppers is triggered
        partial_trajectory = PathMover.engine.generate(
            details.start_point.snapshot,
            running = [ForwardAppendedTrajectoryEnsemble(
                ensemble, 
                details.start[0:details.start_point.index]).can_append, 
                self.length_stopper.can_append]
             )

        details.final = details.start[0:shooting_point] + partial_trajectory
        details.final_point = ShootingPoint(self.selector, details.final,
                                            shooting_point)
        pass
    
class BackwardShootMover(ShootMover):    
    '''
    A pathmover that implements the backward shooting algorithm
    '''
    def _generate(self, details, ensemble):
        print "Shooting backward from frame %d" % details.start_point.index

        # Run until one of the stoppers is triggered
        partial_trajectory = PathMover.engine.generate(
            details.start_point.snapshot.reversed_copy(), 
            running = [BackwardPrependedTrajectoryEnsemble( 
                ensemble, 
                details.start[details.start_point.index + 1:]).can_prepend, 
                self.length_stopper.can_prepend]
        )

        details.final = partial_trajectory.reversed + details.start[details.start_point.index + 1:]
        details.final_point = ShootingPoint(self.selector, details.final, partial_trajectory.frames - 1)
        
        pass

class MixedMover(PathMover):
    '''
    Defines a mover that picks a over from a set of movers with specific
    weights.
    
    Notes
    -----
    Channel functions from self.mover to self. Does NOT work yet. Think
    about a good way to implement this...
    '''
    def __init__(self, movers, weights = None):
        super(MixedMover, self).__init__()

        self.movers = movers

        if weights is None:
            self.weights = [1.0] * len(movers)
        else:
            self.weights = weights
    
    def move(self, trajectory):
        rand = np.random.random() * sum(self.weights)
        idx = 0
        prob = self.weights[0]
        while prob <= rand and idx < len(self.weights):
            idx += 1
            prob += self.weights[idx]

        mover = self.movers[idx]

        sample = mover.move(trajectory)
        setattr(sample.details, 'mover_idx', idx)

        path = Sample(trajectory=sample.trajectory, mover=self, ensemble=mover.ensemble, details=sample.details)
        return path

#############################################################
# The following moves still need to be implemented. Check what excactly they do
#############################################################

class MinusMove(PathMover):
    def move(self, allpaths, state):
        pass

class PathReversal(PathMover):
    def move(self, trajectory, ensemble):
        details = MoveDetails()
        reversed_trajectory = trajectory.reversed()
        details.inputs = [trajectory]
        details.mover = self
        details.final = reversed_trajectory
        details.success = True
        details.acceptance = 1.0
        details.result = reversed_trajectory

        sample = Sample(
            trajectory=details.result,
            mover=self,
            ensemble=ensemble,
            details=details
        )


#############################################################
# The following move should be moved to RETIS and just uses moves. It is not a move itself
#############################################################

class ReplicaExchange(PathMover):
    # TODO: Might put the target ensembles into the Mover instance, which means we need lots of mover instances for all ensemble switches
    def move(self, trajectory1, trajectory2, ensemble1, ensemble2):
        success = True # Change to actual check for swapping
        details1 = MoveDetails()
        details2 = MoveDetails()
        details1.inputs = [trajectory1, trajectory2]
        details2.inputs = [trajectory1, trajectory2]
        setattr(details1, 'ensembles', [ensemble1, ensemble2])
        setattr(details2, 'ensembles', [ensemble1, ensemble2])
        details1.mover = self
        details2.mover = self
        details2.final = trajectory1
        details1.final = trajectory2
        if success:
            # Swap
            details1.success = True
            details2.success = True
            details1.acceptance = 1.0
            details2.acceptance = 1.0
            details1.result = trajectory2
            details2.result = trajectory1
        else:
            # No swap
            details1.success = False
            details2.success = False
            details1.acceptance = 0.0
            details2.acceptance = 0.0
            details1.result = trajectory1
            details2.result = trajectory2

        sample1 = Sample(
            trajectory=details1.result,
            mover=self,
            ensemble=ensemble1,
            details=details1
        )
        sample2 = Sample(
            trajectory=details2.result,
            mover=self,
            ensemble=ensemble2,
            details=details2
            )
        return [sample1, sample2]


class PathMoverFactory(object):
    @staticmethod
    def OneWayShootingSet(selector_set, interface_set):
        if type(selector_set) is not list:
            selector_set = [selector_set]*len(interface_set)
        mover_set = [
            MixedMover([
                ForwardShootMover(sel, iface), 
                BackwardShootMover(sel, iface)
            ])
            for (sel, iface) in zip(selector_set, interface_set)
        ]
        return mover_set

    @staticmethod
    def TwoWayShootingSet():
        pass

    @staticmethod
    def NearestNeighborRepExSet():
        pass
