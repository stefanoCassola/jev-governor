/*---------------------------------------------------------------------------*\
  jevGovernor - in-loop solver governor for OpenFOAM
  Copyright (C) 2026 Stefano Cassola
-------------------------------------------------------------------------------
License
    This file is part of jevGovernor, an add-on for OpenFOAM.

    jevGovernor is free software: you can redistribute it and/or modify it
    under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    jevGovernor is distributed in the hope that it will be useful, but WITHOUT
    ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
    FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
    for more details.

    You should have received a copy of the GNU General Public License
    along with jevGovernor.  If not, see <http://www.gnu.org/licenses/>.

Class
    Foam::functionObjects::jevGovernor

Group
    grpUtilitiesFunctionObjects

Description
    In-loop governor for steady-state (SIMPLE-family) solvers.

    (For transient PIMPLE runs see \c algorithm below.)

    Every \c interval iterations the function object hands the residual
    history to an external decision process (the \c jev-governor sidecar),
    receives one bounded decision and applies it before the next iteration:

    - under-relaxation factors (equations and fields),
    - an optional temporary first-order upwind fallback for the convective
      \c div(phi,...) schemes,
    - an optional request to write and stop.

    Decisions are applied by rewriting \c system/fvSolution and
    \c system/fvSchemes atomically and re-reading them explicitly on all
    ranks, so the mechanism does not depend on \c runTimeModifiable or on
    file-modification time stamps. The original files are backed up as
    \c <file>.jevGovernor.orig and restored at the end of the run.

    The hand-off is file based (directory \c jevGovernor/ in the case):
    \verbatim
        state_<n>.json      written by this function object (master only)
        decision_<n>        written by the sidecar (OpenFOAM dictionary)
    \endverbatim

Usage
    Minimal example by using \c system/controlDict.functions:
    \verbatim
    jevGovernor
    {
        // Mandatory entries
        type            jevGovernor;
        libs            (jevGovernor);

        // Optional entries
        interval        25;          // iterations between decisions
        mode            sync;        // sync | async
        backend         rules;       // rules | jev | mock (sidecar judge)
        fields          (U p k epsilon);
        momentum        U;           // governed equation
        followers       (k epsilon); // equations that follow the momentum move
        pressure        p;           // field relaxed by the SIMPLE rule
        bounds          (0.3 0.95);  // limits of the momentum factor
        upwindFallback  yes;
        launchSidecar   yes;
        sidecarCommand  "jev-governor";
        timeout         120;         // [s] sync wait before holding
        restoreOnEnd    yes;

        // Transient mode: govern the non-final factors of U and p in the
        // PIMPLE outer loop; cost = outer iterations per time step
        algorithm       PIMPLE;      // SIMPLE | PIMPLE (default: PIMPLE if
                                     // PIMPLE.nOuterCorrectors > 1)
        pressureBounds  (0.1 0.9);
        floor           3;           // outer iterations at which to stop tuning
        checkpoint      no;          // writeNow before a trial increase
    }
    \endverbatim

SourceFiles
    jevGovernor.C

\*---------------------------------------------------------------------------*/

#ifndef Foam_functionObjects_jevGovernor_H
#define Foam_functionObjects_jevGovernor_H

#include "fvMeshFunctionObject.H"
#include "HashTable.H"
#include "DynamicList.H"
#include "Tuple2.H"
#include "Switch.H"

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

namespace Foam
{
namespace functionObjects
{

/*---------------------------------------------------------------------------*\
                         Class jevGovernor Declaration
\*---------------------------------------------------------------------------*/

class jevGovernor
:
    public fvMeshFunctionObject
{
    // Private Data

        //- Copy of the function object dictionary (forwarded to the sidecar)
        dictionary dict_;

        //- Iterations between decisions
        label interval_;

        //- Block until the decision arrives (sync) or apply it late (async)
        bool sync_;

        //- Monitored fields
        wordList fields_;

        //- Governed momentum equation, followers and pressure field
        word momentum_;
        wordList followers_;
        word pressure_;

        //- Allow the first-order upwind fallback
        bool upwindFallback_;

        //- Start (and stop) the sidecar from the function object
        bool launchSidecar_;
        string sidecarCommand_;

        //- Maximum wait for a decision in sync mode [s]
        scalar timeout_;

        //- Restore the original system files at the end of the run
        bool restoreOnEnd_;

        //- Residual samples since the last hand-off: (iteration, residuals)
        DynamicList<Tuple2<label, scalarList>> samples_;

        //- Iterations of pending (not yet answered) hand-offs
        DynamicList<label> pending_;

        //- Upwind fallback currently active
        bool upwindActive_;

        //- Original divSchemes entries (before the fallback)
        dictionary origDivSchemes_;

        //- Iteration at which the relaxation factors were last changed (-1: never)
        label lastApplied_;


      // Transient (PIMPLE) mode

        //- One time step of the PIMPLE outer loop
        struct stepRecord
        {
            label index;
            label nOuter;
            label cls;              // 0 converged, 1 slow-capped, 2 unstable
            scalar aU;
            scalar ap;
            scalar rate;            // slowest equation: decades per outer iteration
            scalar rises;           // slowest equation: fraction of rises
            scalarList seqU;        // initial residual per outer iteration
            scalarList seqP;
        };

        //- Govern the PIMPLE outer loop instead of a steady SIMPLE run
        bool pimple_;

        //- Limits of the pressure factor (PIMPLE mode)
        Pair<scalar> pBounds_;

        //- Write a time directory before a trial increase is applied
        bool checkpoint_;

        //- Steps since the last hand-off
        DynamicList<stepRecord> steps_;

        //- Guard actions since the last hand-off (JSON objects)
        DynamicList<string> events_;

        //- Guard data from the last decision: reference outer iterations per
        //- step and the open trial (an increase that has not been judged yet)
        scalar guardRef_;
        word trialFactor_;
        scalar trialFrom_;
        label trialId_;
        label trialAt_;
        label guardAt_;

        bool initialised_;
        label nDecisions_;


    // Private Member Functions

        //- Case-local hand-off directory
        fileName dir() const;

        //- Largest-component initial residual of a field, or -1 if not solved
        scalar initialResidual(const word& fieldName) const;

        template<class Type>
        bool initialResidualType(const word& fieldName, scalar& res) const;

        //- Convergence target of a field from residualControl (0 if none)
        scalar target(const word& fieldName) const;

        //- One-off set-up: directory, backups, sidecar
        void initialise();

        //- Back up (or restore a stale back-up of) a system file
        void backup(const word& sysFile) const;
        void restore(const word& sysFile) const;

        //- Write state_<n>.json (master only)
        void writeState(const label n) const;

        //- Wait for / pick up decisions; return true if one was applied
        bool pollDecisions(const bool block);

        //- Apply a decision on all ranks
        void apply(const dictionary& decision);

        //- Rewrite a registered system dictionary atomically, then re-read
        void rewrite(const word& sysFile, const dictionary& content) const;


      // Transient (PIMPLE) mode

        //- Current non-final factors (1 if not relaxed)
        scalar factorU() const;
        scalar factorP() const;

        //- Outer-loop record of the time step that has just finished
        stepRecord sampleStep() const;

        //- Code-only guard, after every time step. True if it changed factors
        bool stepGuard(const stepRecord& step);

        //- Set the two non-final factors on all ranks
        void setFactors(const scalar aU, const scalar ap);

        //- PIMPLE-mode body of execute() and of the state file
        bool executePimple();
        void writeStepsJson(Ostream& os) const;

        //- No copy construct, no copy assignment
        jevGovernor(const jevGovernor&) = delete;
        void operator=(const jevGovernor&) = delete;


public:

    //- Runtime type information
    TypeName("jevGovernor");


    // Constructors

        //- Construct from Time and dictionary
        jevGovernor
        (
            const word& name,
            const Time& runTime,
            const dictionary& dict
        );


    //- Destructor
    virtual ~jevGovernor() = default;


    // Member Functions

        //- Read the settings
        virtual bool read(const dictionary& dict);

        //- Sample residuals, hand off and apply decisions
        virtual bool execute();

        //- Nothing to write
        virtual bool write();

        //- Stop the sidecar and restore the system files
        virtual bool end();
};


// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

} // End namespace functionObjects
} // End namespace Foam

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

#endif

// ************************************************************************* //
