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

Description
    Transient (PIMPLE) mode: what is governed are the non-final
    under-relaxation factors of the momentum equation and the pressure field
    in the outer loop; the cost is the number of outer iterations per time
    step. The guard runs here, after every time step, without the sidecar.

\*---------------------------------------------------------------------------*/

#include "jevGovernor.H"
#include "fvMesh.H"
#include "Time.H"
#include "SolverPerformance.H"
#include "OSspecific.H"
#include "Pstream.H"

#include <cmath>
#include <fstream>

// * * * * * * * * * * * * * * * Local Functions * * * * * * * * * * * * * * //

namespace Foam
{

static void writeJsonSeq(Ostream& os, const scalarList& seq)
{
    os << '[';
    forAll(seq, i)
    {
        if (i) os << ", ";
        if (std::isfinite(seq[i])) os << seq[i]; else os << "null";
    }
    os << ']';
}


//- One multiplicative move of tau = a/(1-a), limited to [lo, hi]
static scalar tauMove(const scalar a, const scalar mult, const Pair<scalar>& b)
{
    const scalar tau = min(a, 0.999)/(1 - min(a, 0.999))*mult;
    return max(b.first(), min(b.second(), tau/(1 + tau)));
}

} // End namespace Foam


// * * * * * * * * * * * * * Private Member Functions  * * * * * * * * * * * //

Foam::scalar Foam::functionObjects::jevGovernor::factorU() const
{
    return
    (
        mesh_.relaxEquation(momentum_)
      ? mesh_.equationRelaxationFactor(momentum_)
      : 1
    );
}


Foam::scalar Foam::functionObjects::jevGovernor::factorP() const
{
    return
    (
        mesh_.relaxField(pressure_)
      ? mesh_.fieldRelaxationFactor(pressure_)
      : 1
    );
}


Foam::functionObjects::jevGovernor::stepRecord
Foam::functionObjects::jevGovernor::sampleStep() const
{
    stepRecord step;
    step.index = time_.timeIndex();
    step.aU = factorU();
    step.ap = factorP();
    step.cls = 0;
    step.rate = 0;
    step.rises = 0;

    const dictionary& pimpleDict = mesh_.solutionDict().subDict("PIMPLE");
    const label cap = pimpleDict.getOrDefault<label>("nOuterCorrectors", 1);
    const label inner =
        max(label(1), pimpleDict.getOrDefault<label>("nCorrectors", 1))
       *(pimpleDict.getOrDefault<label>("nNonOrthogonalCorrectors", 0) + 1);

    // All solves of this time step, in order (cleared when the time index
    // changes): one momentum solve and 'inner' pressure solves per outer
    // iteration
    const dictionary& solverDict = mesh_.data().solverPerformanceDict();

    bool finite = true;

    if (solverDict.found(momentum_))
    {
        const List<SolverPerformance<vector>> sp(solverDict.lookup(momentum_));
        step.seqU.resize(sp.size());
        forAll(sp, i)
        {
            const vector& r = sp[i].initialResidual();
            step.seqU[i] = cmptMax(r);
            for (direction d = 0; d < vector::nComponents; ++d)
            {
                if (!std::isfinite(r[d])) finite = false;
            }
        }
    }

    scalar pBeforeFinal = -1;

    if (solverDict.found(pressure_))
    {
        const List<SolverPerformance<scalar>> sp(solverDict.lookup(pressure_));
        const label nOuterP = sp.size()/inner;
        step.seqP.resize(nOuterP);
        for (label i = 0; i < nOuterP; ++i)
        {
            step.seqP[i] = sp[i*inner].initialResidual();
        }
        forAll(sp, i)
        {
            if (!std::isfinite(sp[i].initialResidual())) finite = false;
        }
        if (nOuterP >= 2)
        {
            // What the residual control saw before the last outer iteration
            pBeforeFinal = sp[(nOuterP - 1)*inner - 1].initialResidual();
        }
    }

    step.nOuter = step.seqU.size() ? step.seqU.size() : step.seqP.size();

    // Capped: the last outer iteration was final because of the cap, not
    // because the residual control was met before it
    const scalar tolU = target(momentum_);
    const scalar tolP = target(pressure_);

    bool capped = false;
    if (step.nOuter >= cap && cap > 1)
    {
        const bool metU =
        (
            tolU <= 0 || step.seqU.size() < 2
         || step.seqU[step.seqU.size() - 2] < tolU
        );
        const bool metP = (tolP <= 0 || pBeforeFinal < 0 || pBeforeFinal < tolP);
        capped = !(metU && metP);
    }

    // Within-step behaviour of the slowest equation. The final iteration is
    // unrelaxed and left out
    const scalar levelU =
    (
        step.seqU.size() && tolU > 0 ? step.seqU.last()/tolU : 0
    );
    const scalar levelP =
    (
        step.seqP.size() && tolP > 0 ? step.seqP.last()/tolP : 0
    );
    const scalarList& seq = (levelP > levelU ? step.seqP : step.seqU);
    const label m = seq.size() - 1;

    if (finite && m >= 2 && seq[0] > 0 && seq[m - 1] > 0)
    {
        step.rate = std::log10(seq[m - 1]/seq[0])/(m - 1);
        label nRises = 0;
        for (label i = 1; i < m; ++i)
        {
            if (seq[i] > 1.05*seq[i - 1]) ++nRises;
        }
        step.rises = scalar(nRises)/(m - 1);
    }

    if (!finite)
    {
        step.cls = 2;
    }
    else if (capped)
    {
        // Slow: falls monotonically but has not arrived. Unstable: the rest
        step.cls = (step.rises < 0.1 && step.rate < 0 ? 1 : 2);
    }

    return step;
}


void Foam::functionObjects::jevGovernor::setFactors
(
    const scalar aU,
    const scalar ap
)
{
    dictionary decision;
    dictionary& relax = decision.subDictOrAdd("relaxationFactors");
    relax.subDictOrAdd("equations").set(momentum_, aU);
    relax.subDictOrAdd("fields").set(pressure_, ap);
    apply(decision);
}


bool Foam::functionObjects::jevGovernor::stepGuard(const stepRecord& step)
{
    const label n = step.index;

    if (n - guardAt_ < max(label(2), interval_/2))
    {
        return false;
    }

    const bool trialOpen =
        (!trialFactor_.empty() && n - trialAt_ <= 2*interval_);

    // The iterations per step creep up before a blow-up
    const bool creep =
    (
        guardRef_ > 0
     && step.nOuter >= max(1.5*guardRef_, guardRef_ + 6)
    );

    const bool unstable = (step.cls == 2 || (creep && step.rises >= 0.1));

    // Smooth but worse (slow-capped, or creeping up without erratic
    // residuals) is not dangerous, and the first steps after any change of a
    // factor need a few extra iterations anyway: judge it after they settled
    const bool worseButSmooth =
    (
        !unstable
     && (step.cls == 1 || creep)
     && (guardRef_ <= 0 || step.nOuter > guardRef_)
     && n - trialAt_ > 2
    );

    scalar aU = step.aU;
    scalar ap = step.ap;
    word action;

    if (trialOpen && (unstable || worseButSmooth))
    {
        // Take the increase back. Unstable: remember the value as a cliff
        action = "take_back";
        (trialFactor_ == momentum_ ? aU : ap) = trialFrom_;
    }
    else if (unstable)
    {
        action = "lower_both";
        const Pair<scalar> uBounds
        (
            dict_.getOrDefault<Pair<scalar>>("bounds", Pair<scalar>(0.3, 0.95))
        );
        aU = tauMove(aU, 1/1.4, uBounds);
        ap = tauMove(ap, 1/1.4, pBounds_);
    }
    else
    {
        // Slow steps without a trial are not a reason to relax more:
        // that would slow the loop further
        return false;
    }

    // Identical data on all ranks, but make sure of it
    Pstream::broadcast(aU);
    Pstream::broadcast(ap);

    OStringStream os;
    os  << "{\"step\": " << n << ", \"action\": \"" << action.c_str()
        << "\", \"reason\": \""
        << (step.cls == 2 ? "unstable" : creep ? "creep" : "slow_capped")
        << "\", \"cliff\": " << (unstable ? "true" : "false")
        << ", \"factor\": \""
        << (action == "take_back" ? trialFactor_.c_str() : "both")
        << "\", \"n_outer\": " << step.nOuter
        << ", \"from\": {\"U\": " << step.aU << ", \"p\": " << step.ap
        << "}, \"to\": {\"U\": " << aU << ", \"p\": " << ap << "}}";
    events_.append(os.str());

    Info<< type() << " " << name() << ": guard at step " << n << ": "
        << action << " (" << step.nOuter << " outer iterations, "
        << (step.cls == 2 ? "unstable" : creep ? "creep" : "slow-capped")
        << "), " << momentum_ << " " << step.aU << " -> " << aU << ", "
        << pressure_ << " " << step.ap << " -> " << ap << endl;

    trialFactor_.clear();
    trialId_ = -1;
    trialAt_ = -1;
    guardAt_ = n;

    setFactors(aU, ap);

    return true;
}


void Foam::functionObjects::jevGovernor::writeStepsJson(Ostream& os) const
{
    const dictionary& pimpleDict = mesh_.solutionDict().subDict("PIMPLE");

    os  << "  \"cap\": "
        << pimpleDict.getOrDefault<label>("nOuterCorrectors", 1) << ",\n"
        << "  \"events\": [";
    forAll(events_, i)
    {
        os << (i ? ",\n    " : "\n    ") << events_[i].c_str();
    }
    os  << "\n  ],\n"
        << "  \"steps\": [";
    forAll(steps_, i)
    {
        const stepRecord& s = steps_[i];
        os  << (i ? ",\n    " : "\n    ")
            << "{\"step\": " << s.index << ", \"n_outer\": " << s.nOuter
            << ", \"class\": " << s.cls << ", \"U\": ";
        writeJsonSeq(os, s.seqU);
        os  << ", \"p\": ";
        writeJsonSeq(os, s.seqP);
        os  << "}";
    }
    os  << "\n  ]\n}\n";
}


bool Foam::functionObjects::jevGovernor::executePimple()
{
    const stepRecord step(sampleStep());
    const label n = step.index;

    steps_.append(step);

    if (Pstream::master())
    {
        const fileName csv(dir()/"steps.csv");
        const bool isNew = !isFile(csv);
        std::ofstream os(csv.c_str(), std::ios::app);
        if (isNew)
        {
            os << "step,time,n_outer,class,aU,ap\n";
        }
        os  << n << ',' << time_.value() << ',' << step.nOuter << ','
            << (step.cls == 0 ? "ok" : step.cls == 1 ? "slow_capped" : "unstable")
            << ',' << step.aU << ',' << step.ap << '\n';
    }

    // 1. guard (always synchronous)  2. decisions that have arrived
    // 3. hand-off when one is due
    stepGuard(step);

    if (!sync_)
    {
        pollDecisions(false);
    }

    if (n % interval_ == 0)
    {
        writeState(n);
        pending_.append(n);
        steps_.clear();
        events_.clear();

        if (sync_)
        {
            pollDecisions(true);
        }
    }

    return true;
}


// ************************************************************************* //
