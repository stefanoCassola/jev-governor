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

\*---------------------------------------------------------------------------*/

#include "jevGovernor.H"
#include "addToRunTimeSelectionTable.H"
#include "fvMesh.H"
#include "volFields.H"
#include "Time.H"
#include "OFstream.H"
#include "IFstream.H"
#include "ITstream.H"
#include "primitiveEntry.H"
#include "SolverPerformance.H"
#include "OSspecific.H"
#include "Pstream.H"

#include <cmath>
#include <unistd.h>

// * * * * * * * * * * * * * * Static Data Members * * * * * * * * * * * * * //

namespace Foam
{
namespace functionObjects
{
    defineTypeNameAndDebug(jevGovernor, 0);
    addToRunTimeSelectionTable(functionObject, jevGovernor, dictionary);
}
}


// * * * * * * * * * * * * * * * Local Functions * * * * * * * * * * * * * * //

namespace Foam
{

//- JSON number, or null for non-finite values
static void writeJsonScalar(Ostream& os, const scalar v)
{
    if (std::isfinite(v))
    {
        os << v;
    }
    else
    {
        os << "null";
    }
}


//- JSON list of quoted words
static void writeJsonWords(Ostream& os, const wordList& words)
{
    os << '[';
    forAll(words, i)
    {
        if (i) os << ", ";
        os << '"' << words[i].c_str() << '"';
    }
    os << ']';
}

} // End namespace Foam


// * * * * * * * * * * * * * Private Member Functions  * * * * * * * * * * * //

Foam::fileName Foam::functionObjects::jevGovernor::dir() const
{
    return time_.globalPath()/"jevGovernor";
}


template<class Type>
bool Foam::functionObjects::jevGovernor::initialResidualType
(
    const word& fieldName,
    scalar& res
) const
{
    typedef GeometricField<Type, fvPatchField, volMesh> volFieldType;

    if (!foundObject<volFieldType>(fieldName))
    {
        return false;
    }

    const dictionary& solverDict = mesh_.data().solverPerformanceDict();

    if (solverDict.found(fieldName))
    {
        const List<SolverPerformance<Type>> sp(solverDict.lookup(fieldName));

        if (sp.size())
        {
            // Largest component of a finite residual, otherwise non-finite
            res = cmptMax(sp.first().initialResidual());

            const Type& r = sp.first().initialResidual();
            for (direction cmpt = 0; cmpt < pTraits<Type>::nComponents; ++cmpt)
            {
                if (!std::isfinite(component(r, cmpt)))
                {
                    res = component(r, cmpt);
                }
            }
        }
    }

    return true;
}


Foam::scalar Foam::functionObjects::jevGovernor::initialResidual
(
    const word& fieldName
) const
{
    scalar res = -1;

    initialResidualType<scalar>(fieldName, res)
 || initialResidualType<vector>(fieldName, res)
 || initialResidualType<sphericalTensor>(fieldName, res)
 || initialResidualType<symmTensor>(fieldName, res)
 || initialResidualType<tensor>(fieldName, res);

    return res;
}


Foam::scalar Foam::functionObjects::jevGovernor::target
(
    const word& fieldName
) const
{
    const dictionary& solDict = mesh_.solutionDict();

    for (const word& algo : {word("SIMPLE"), word("PIMPLE")})
    {
        const dictionary* algoPtr = solDict.findDict(algo);

        if (algoPtr)
        {
            const dictionary* rcPtr = algoPtr->findDict("residualControl");

            if (rcPtr)
            {
                const entry* ePtr =
                    rcPtr->findEntry(fieldName, keyType::REGEX);

                if (ePtr && ePtr->isStream())
                {
                    return ePtr->get<scalar>();
                }
                else if (ePtr && ePtr->isDict())
                {
                    return ePtr->dict().getOrDefault<scalar>("tolerance", 0);
                }
            }
        }
    }

    return 0;
}


void Foam::functionObjects::jevGovernor::backup(const word& sysFile) const
{
    if (!Pstream::master())
    {
        return;
    }

    const fileName file(time_.globalPath()/time_.system()/mesh_.dbDir()/sysFile);
    const fileName orig(file + ".jevGovernor.orig");

    if (isFile(orig))
    {
        // Left over from a run that did not end cleanly: start from it
        Info<< type() << " " << name() << ": restoring " << sysFile
            << " from stale back-up" << endl;
        cp(orig, file);
    }
    else
    {
        cp(file, orig);
    }
}


void Foam::functionObjects::jevGovernor::restore(const word& sysFile) const
{
    if (!Pstream::master())
    {
        return;
    }

    const fileName file(time_.globalPath()/time_.system()/mesh_.dbDir()/sysFile);
    const fileName orig(file + ".jevGovernor.orig");

    if (isFile(orig))
    {
        mv(orig, file);
    }
}


void Foam::functionObjects::jevGovernor::initialise()
{
    if (Pstream::master())
    {
        mkDir(dir());

        // Remove the hand-off files of a previous run
        for (const fileName& f : readDir(dir(), fileName::FILE))
        {
            if
            (
                f.starts_with("state_")
             || f.starts_with("decision_")
             || f == "done"
                // a run restarted from a checkpoint continues the step record
             || (f == "steps.csv" && time_.timeIndex() <= 1)
            )
            {
                rm(dir()/f);
            }
        }
    }

    backup("fvSolution");
    backup("fvSchemes");

    // A stale back-up may have replaced the files: make all ranks consistent
    label barrier = 0;
    Pstream::broadcast(barrier);
    const_cast<fvMesh&>(mesh_).fvSolution::read();
    const_cast<fvMesh&>(mesh_).fvSchemes::read();

    if (launchSidecar_ && Pstream::master())
    {
        const string cmd
        (
            sidecarCommand_
          + " serve --case '" + time_.globalPath() + "'"
          + (
                Foam::getEnv("JEV_GOVERNOR_BACKEND").empty()
              ? " --backend " + dict_.getOrDefault<word>("backend", "rules")
              : string()
            )
          + " --model " + dict_.getOrDefault<word>("model", "jev-1.13.0")
          + " --pid " + Foam::name(pid())
          + " > '" + (dir()/"sidecar.log") + "' 2>&1 &"
        );

        Info<< type() << " " << name() << ": starting sidecar: "
            << cmd.c_str() << endl;

        Foam::system(cmd);
    }

    initialised_ = true;
}


void Foam::functionObjects::jevGovernor::writeState(const label n) const
{
    if (!Pstream::master())
    {
        return;
    }

    const fileName tmp(dir()/("state_" + Foam::name(n) + ".json.tmp"));
    const fileName file(dir()/("state_" + Foam::name(n) + ".json"));

    {
        OFstream os(tmp);
        os.precision(8);

        os  << "{\n"
            << "  \"iteration\": " << n << ",\n"
            << "  \"time\": " << time_.value() << ",\n"
            << "  \"solver_pid\": " << pid() << ",\n"
            << "  \"config\": {\n"
            << "    \"interval\": " << interval_ << ",\n"
            << "    \"mode\": \"" << (sync_ ? "sync" : "async") << "\",\n"
            << "    \"algorithm\": \"" << (pimple_ ? "PIMPLE" : "SIMPLE")
            << "\",\n"
            << "    \"pressure_bounds\": [" << pBounds_.first() << ", "
            << pBounds_.second() << "],\n"
            << "    \"floor\": " << dict_.getOrDefault<scalar>("floor", 3)
            << ",\n"
            << "    \"checkpoint\": " << (checkpoint_ ? "true" : "false")
            << ",\n"
            << "    \"backend\": \""
            << dict_.getOrDefault<word>("backend", "rules").c_str() << "\",\n"
            << "    \"model\": \""
            << dict_.getOrDefault<word>("model", "jev-1.13.0").c_str()
            << "\",\n"
            << "    \"momentum\": \"" << momentum_.c_str() << "\",\n"
            << "    \"pressure\": \"" << pressure_.c_str() << "\",\n"
            << "    \"followers\": ";
        writeJsonWords(os, followers_);

        const Pair<scalar> bounds
        (
            dict_.getOrDefault<Pair<scalar>>("bounds", Pair<scalar>(0.3, 0.95))
        );

        os  << ",\n"
            << "    \"bounds\": [" << bounds.first() << ", "
            << bounds.second() << "],\n"
            << "    \"upwind_fallback\": "
            << (upwindFallback_ ? "true" : "false") << ",\n"
            << "    \"description\": \""
            << dict_.getOrDefault<string>("description", "").c_str()
            << "\"\n"
            << "  },\n";

        os  << "  \"fields\": ";
        writeJsonWords(os, fields_);
        os  << ",\n  \"targets\": {";
        forAll(fields_, i)
        {
            if (i) os << ", ";
            os << '"' << fields_[i].c_str() << "\": " << target(fields_[i]);
        }
        os  << "},\n";

        // Current controls
        wordList eqns(followers_);
        eqns.append(momentum_);

        os  << "  \"controls\": {\n    \"equations\": {";
        forAll(eqns, i)
        {
            if (i) os << ", ";
            os << '"' << eqns[i].c_str() << "\": ";
            if (mesh_.relaxEquation(eqns[i]))
            {
                os << mesh_.equationRelaxationFactor(eqns[i]);
            }
            else
            {
                os << "null";
            }
        }
        os  << "},\n    \"fields\": {\"" << pressure_.c_str() << "\": ";
        if (mesh_.relaxField(pressure_))
        {
            os << mesh_.fieldRelaxationFactor(pressure_);
        }
        else
        {
            os << "null";
        }

        bool consistent = false;
        if (const dictionary* d = mesh_.solutionDict().findDict("SIMPLE"))
        {
            consistent = d->getOrDefault("consistent", false);
        }

        os  << "},\n"
            << "    \"consistent\": " << (consistent ? "true" : "false")
            << ",\n"
            << "    \"upwind\": " << (upwindActive_ ? "true" : "false")
            << ",\n"
            << "    \"last_change_applied_at\": ";
        if (lastApplied_ < 0)
        {
            os << "null";
        }
        else
        {
            os << lastApplied_;
        }
        os  << "\n  },\n";

        if (pimple_)
        {
            writeStepsJson(os);
        }
        else
        {
            os  << "  \"samples\": [";
            forAll(samples_, i)
            {
                os << (i ? ",\n    " : "\n    ") << '[' << samples_[i].first()
                   << ", [";
                const scalarList& r = samples_[i].second();
                forAll(r, j)
                {
                    if (j) os << ", ";
                    writeJsonScalar(os, r[j]);
                }
                os << "]]";
            }
            os  << "\n  ]\n}\n";
        }
    }

    mv(tmp, file);
}


void Foam::functionObjects::jevGovernor::rewrite
(
    const word& sysFile,
    const dictionary& content
) const
{
    if (Pstream::master())
    {
        const fileName file
        (
            time_.globalPath()/time_.system()/mesh_.dbDir()/sysFile
        );
        const fileName tmp(file + ".jevGovernor.tmp");

        {
            OFstream os(tmp);

            IOobject::writeBanner(os);
            os  << "FoamFile\n{\n"
                << "    version     2.0;\n"
                << "    format      ascii;\n"
                << "    class       dictionary;\n"
                << "    object      " << sysFile << ";\n"
                << "}\n";
            IOobject::writeDivider(os) << nl
                << "// Rewritten by jevGovernor; the original is kept as "
                << sysFile << ".jevGovernor.orig" << nl << nl;

            content.write(os, false);

            IOobject::writeEndDivider(os);
        }

        mv(tmp, file);
    }

    // The other ranks must not read before the master has finished
    label barrier = 0;
    Pstream::broadcast(barrier);
}


void Foam::functionObjects::jevGovernor::apply(const dictionary& decision)
{
    fvMesh& mesh = const_cast<fvMesh&>(mesh_);

    if (pimple_ && decision.found("guardRef"))
    {
        // What the per-step guard needs until the next decision
        guardRef_ = decision.get<scalar>("guardRef");

        if (const dictionary* trialPtr = decision.findDict("trial"))
        {
            const word factor(trialPtr->get<word>("factor"));
            const label id(trialPtr->get<label>("id"));

            if (id != trialId_)
            {
                trialId_ = id;
                trialAt_ = time_.timeIndex();

                if (checkpoint_)
                {
                    Info<< "    checkpoint before the trial increase" << endl;
                    const_cast<Time&>(time_).writeNow();
                }
            }
            trialFactor_ = factor;
            trialFrom_ = trialPtr->get<scalar>("from");
        }
        else
        {
            trialFactor_.clear();
            trialId_ = -1;
            trialAt_ = -1;
        }
    }

    // Relaxation factors
    if (const dictionary* relaxPtr = decision.findDict("relaxationFactors"))
    {
        dictionary sol(static_cast<const dictionary&>(mesh_.solutionDict()));
        dictionary& relax = sol.subDictOrAdd("relaxationFactors");

        for (const word& group : {word("equations"), word("fields")})
        {
            if (const dictionary* gPtr = relaxPtr->findDict(group))
            {
                dictionary& target = relax.subDictOrAdd(group);

                for (const entry& e : *gPtr)
                {
                    // A literal key takes precedence over any pattern
                    target.set(word(e.keyword()), e.get<scalar>());
                }
            }
        }

        rewrite("fvSolution", sol);
        mesh.fvSolution::read();
        lastApplied_ = time_.timeIndex();

        Info<< "    relaxationFactors "
            << mesh_.solutionDict().subDict("relaxationFactors") << endl;
    }

    // First-order upwind fallback for the convective schemes
    const bool upwind = decision.getOrDefault("upwind", upwindActive_);

    if (upwindFallback_ && upwind != upwindActive_)
    {
        dictionary schemes(static_cast<const dictionary&>(mesh_.schemesDict()));
        dictionary& div = schemes.subDictOrAdd("divSchemes");

        if (upwind)
        {
            origDivSchemes_ = div;

            for (const entry& e : origDivSchemes_)
            {
                const word key(e.keyword());

                if (!key.starts_with("div(phi,") || !e.isStream())
                {
                    continue;
                }

                ITstream& is = e.stream();
                const bool bounded =
                (
                    is.size() && is.front().isWord()
                 && is.front().wordToken() == "bounded"
                );

                ITstream scheme
                (
                    bounded ? "bounded Gauss upwind" : "Gauss upwind"
                );

                div.set(primitiveEntry(e.keyword(), scheme));
            }
        }
        else
        {
            div = origDivSchemes_;
        }

        rewrite("fvSchemes", schemes);
        mesh.fvSchemes::read();
        upwindActive_ = upwind;

        Info<< "    upwind fallback " << (upwind ? "ON" : "OFF") << nl
            << "    divSchemes " << mesh_.schemesDict().subDict("divSchemes")
            << endl;
    }

    if (decision.getOrDefault("stop", false))
    {
        Info<< "    stop requested: writing and ending the run" << endl;
        const_cast<Time&>(time_).stopAt(Time::saWriteNow);
    }
}


bool Foam::functionObjects::jevGovernor::pollDecisions(const bool block)
{
    // All decisions that have arrived, oldest first. None is dropped: a later
    // "hold" must not swallow an earlier change.
    DynamicList<label> applied;
    DynamicList<dictionary> decisions;

    if (Pstream::master() && pending_.size())
    {
        scalar waited = 0;

        do
        {
            for (const label n : pending_)
            {
                const fileName file(dir()/("decision_" + Foam::name(n)));

                if (isFile(file))
                {
                    IFstream is(file);
                    decisions.append(dictionary(is));
                    applied.append(n);
                }
            }

            // Sync mode waits for the newest hand-off
            const bool complete =
                applied.size() && applied.last() == pending_.last();

            if (!block || complete)
            {
                break;
            }

            applied.clear();
            decisions.clear();

            ::usleep(5000);
            waited += 0.005;

            if (timeout_ > 0 && waited > timeout_)
            {
                WarningInFunction
                    << "No decision for iteration " << pending_.last()
                    << " within " << timeout_ << " s: holding the"
                    << " current settings. Is the sidecar running? See "
                    << dir()/"sidecar.log" << endl;
                break;
            }
        }
        while (true);
    }

    Pstream::broadcast(applied);

    if (applied.empty())
    {
        return false;
    }

    Pstream::broadcast(decisions);

    // Forget these and all older hand-offs (async: skipped by the sidecar)
    DynamicList<label> newer;
    for (const label n : pending_)
    {
        if (n > applied.last()) newer.append(n);
    }
    pending_.transfer(newer);

    forAll(applied, i)
    {
        if (pimple_ && applied[i] < guardAt_)
        {
            Info<< type() << " " << name() << ": decision for " << applied[i]
                << " dropped, the guard acted at " << guardAt_ << endl;
            continue;
        }

        ++nDecisions_;

        Info<< type() << " " << name() << ": iteration " << time_.timeIndex()
            << ", decision for " << applied[i] << ": "
            << decisions[i].getOrDefault<string>("note", "").c_str() << endl;

        apply(decisions[i]);
    }

    return true;
}


// * * * * * * * * * * * * * * * * Constructors  * * * * * * * * * * * * * * //

Foam::functionObjects::jevGovernor::jevGovernor
(
    const word& name,
    const Time& runTime,
    const dictionary& dict
)
:
    fvMeshFunctionObject(name, runTime, dict),
    dict_(),
    interval_(25),
    sync_(true),
    fields_(),
    momentum_("U"),
    followers_(),
    pressure_("p"),
    upwindFallback_(true),
    launchSidecar_(true),
    sidecarCommand_("jev-governor"),
    timeout_(120),
    restoreOnEnd_(true),
    samples_(),
    pending_(),
    upwindActive_(false),
    origDivSchemes_(),
    lastApplied_(-1),
    pimple_(false),
    pBounds_(0.1, 0.9),
    checkpoint_(false),
    steps_(),
    events_(),
    guardRef_(-1),
    trialFactor_(),
    trialFrom_(0),
    trialId_(-1),
    trialAt_(-1),
    guardAt_(-1000000),
    initialised_(false),
    nDecisions_(0)
{
    read(dict);
}


// * * * * * * * * * * * * * * * Member Functions  * * * * * * * * * * * * * //

bool Foam::functionObjects::jevGovernor::read(const dictionary& dict)
{
    if (!fvMeshFunctionObject::read(dict))
    {
        return false;
    }

    dict_ = dict;

    interval_ = dict.getOrDefault<label>("interval", 25);
    if (interval_ < 1)
    {
        FatalIOErrorInFunction(dict)
            << "interval must be >= 1" << exit(FatalIOError);
    }

    const word mode(dict.getOrDefault<word>("mode", "sync"));
    if (mode != "sync" && mode != "async")
    {
        FatalIOErrorInFunction(dict)
            << "mode must be sync or async, not " << mode
            << exit(FatalIOError);
    }
    sync_ = (mode == "sync");

    momentum_ = dict.getOrDefault<word>("momentum", "U");
    pressure_ = dict.getOrDefault<word>("pressure", "p");
    followers_ = dict.getOrDefault<wordList>("followers", wordList());

    if (!dict.readIfPresent("fields", fields_))
    {
        fields_ = followers_;
        fields_.append(momentum_);
        fields_.append(pressure_);
    }

    // Transient mode: explicit, or detected from the PIMPLE outer loop
    word algorithm;
    if (!dict.readIfPresent("algorithm", algorithm))
    {
        const dictionary* pimplePtr = mesh_.solutionDict().findDict("PIMPLE");
        algorithm =
        (
            pimplePtr
         && pimplePtr->getOrDefault<label>("nOuterCorrectors", 1) > 1
          ? "PIMPLE"
          : "SIMPLE"
        );
    }
    if (algorithm != "SIMPLE" && algorithm != "PIMPLE")
    {
        FatalIOErrorInFunction(dict)
            << "algorithm must be SIMPLE or PIMPLE, not " << algorithm
            << exit(FatalIOError);
    }
    pimple_ = (algorithm == "PIMPLE");

    if (pimple_)
    {
        if (followers_.size())
        {
            WarningInFunction
                << "followers are ignored in PIMPLE mode: only the non-final"
                << " factors of " << momentum_ << " and " << pressure_
                << " are governed" << endl;
            followers_.clear();
        }
        fields_ = wordList({momentum_, pressure_});
        if (!dict.found("interval"))
        {
            interval_ = 10;
        }
        pBounds_ =
            dict.getOrDefault<Pair<scalar>>("pressureBounds", pBounds_);
        checkpoint_ = dict.getOrDefault("checkpoint", false);
    }

    upwindFallback_ = dict.getOrDefault("upwindFallback", true);
    launchSidecar_ = dict.getOrDefault("launchSidecar", true);
    sidecarCommand_ =
        dict.getOrDefault<string>("sidecarCommand", "jev-governor");
    timeout_ = dict.getOrDefault<scalar>("timeout", 120);
    restoreOnEnd_ = dict.getOrDefault("restoreOnEnd", true);

    return true;
}


bool Foam::functionObjects::jevGovernor::execute()
{
    if (!initialised_)
    {
        initialise();
    }

    if (pimple_)
    {
        return executePimple();
    }

    const label n = time_.timeIndex();

    scalarList res(fields_.size());
    forAll(fields_, i)
    {
        res[i] = initialResidual(fields_[i]);
    }
    samples_.append(Tuple2<label, scalarList>(n, res));

    if (!sync_)
    {
        pollDecisions(false);
    }

    if (n % interval_ == 0)
    {
        writeState(n);
        pending_.append(n);
        samples_.clear();

        if (sync_)
        {
            pollDecisions(true);
        }
    }

    return true;
}


bool Foam::functionObjects::jevGovernor::write()
{
    return true;
}


bool Foam::functionObjects::jevGovernor::end()
{
    if (!initialised_)
    {
        return true;
    }

    if (Pstream::master())
    {
        OFstream os(dir()/"done");
        os  << time_.timeIndex() << endl;
    }

    Info<< type() << " " << name() << ": " << nDecisions_
        << " decisions applied";

    if (upwindActive_)
    {
        Info<< nl
            << "    WARNING: the run ended with the first-order upwind"
            << " fallback active." << nl
            << "    The solution is first-order accurate in the convective"
            << " terms.";
    }
    Info<< endl;

    if (restoreOnEnd_)
    {
        restore("fvSolution");
        restore("fvSchemes");
    }

    return true;
}


// ************************************************************************* //
