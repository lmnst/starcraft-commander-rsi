// Human-command adapter. Baseline.dll is loaded unchanged and owns the economy.
#include <windows.h>
#include <BWAPI.h>
#include "NativeViewport.h"
#include <bwem.h>
#include "MatchIdentity.h"
#include "CommanderRules.h"
#include "CommanderScout.h"
#include "CommanderKeys.h"
#include "CommanderMining.h"
#include "ProbeCommandGuard.h"
#include "CommanderOrders.h"
#include "CommanderFlight.h"
#include "CommanderTargeting.h"
#include "CommanderRaid.h"
#include "CommanderMainArea.h"
#include "CommanderClear.h"
#include "CommanderDetection.h"
#include "CommanderStandby.h"
#include "CommanderDefence.h"
#include "CommanderBases.h"
#include "CommanderRendezvous.h"
#include "CommanderAssembly.h"
#include "CommanderHudText.h"
#include "CommanderHudRaster.h"
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <map>
#include <set>
#include <string>
#include <vector>
using namespace BWAPI;
using namespace UnitTypes;
namespace Rules = CommanderRules;
namespace Plans = CommanderOrders;
namespace Flight = CommanderFlight;
namespace Targeting = CommanderTargeting;
namespace Rally = CommanderRendezvous;
namespace Standby = CommanderStandby;
namespace MultiBase = CommanderBases;
static Game* game = nullptr;
static std::vector<int> keys;
static bool commandHeld[11] = {};
static ULONGLONG lastInput = 0;
static LPARAM mousePoint = -1;

static LRESULT CALLBACK input(int code, WPARAM removal, LPARAM data) {
    if (code == HC_ACTION && removal == PM_REMOVE) {
        MSG& m = *reinterpret_cast<MSG*>(data);
        if ((m.message == WM_MOUSEMOVE && m.lParam != mousePoint) ||
            (m.message >= WM_LBUTTONDOWN && m.message <= WM_MOUSEHWHEEL) || m.message == WM_KEYDOWN || m.message==WM_SYSKEYDOWN)
            lastInput = GetTickCount64();
        if (m.message == WM_MOUSEMOVE) mousePoint = m.lParam;
        int heldIndex=CommanderKeys::heldIndex(int(m.wParam));
        if ((m.message == WM_KEYDOWN || m.message == WM_KEYUP || m.message==WM_SYSKEYDOWN || m.message==WM_SYSKEYUP) && heldIndex>=0) {
            bool& held = commandHeld[heldIndex];
            int shortcut=CommanderKeys::shortcut(int(m.wParam),GetKeyState(VK_CONTROL)&0x8000,
                GetKeyState(VK_SHIFT)&0x8000,(GetKeyState(VK_MENU)&0x8000) || (m.lParam&(1L<<29)));
            const bool down = m.message == WM_KEYDOWN || m.message==WM_SYSKEYDOWN;
            if (down && shortcut && !held && !(m.lParam & (1L << 30))) {
                keys.push_back(shortcut);
                held = true;
            }
            // Plain top-row digits and OEM minus own human shortcuts. Consume
            // repeats and matching releases; modified keys stay native.
            // Numpad digits and all function keys are outside this range.
            if (held || (down && shortcut)) {
                if (!down) held = false;
                m.message = WM_NULL; m.wParam = 0; m.lParam = 0;
            }
        }
    }
    return CallNextHookEx(nullptr, code, removal, data);
}
static std::string env(const char* name, const char* fallback = "") {
    const char* value = std::getenv(name); return value ? value : fallback;
}
static BOOL CALLBACK findGameWindow(HWND window, LPARAM result) {
    DWORD pid = 0; GetWindowThreadProcessId(window, &pid);
    if (pid != GetCurrentProcessId() || !IsWindowVisible(window) || GetWindow(window, GW_OWNER)) return TRUE;
    *reinterpret_cast<HWND*>(result) = window; return FALSE;
}

class CommanderLayer final : public AIModule {
    struct Memory { Position position; UnitType type; TilePosition tile; };
    HMODULE library = nullptr;
    AIModule* delegate = nullptr;
    HHOOK hook = nullptr;
    bool human=false, ended=false, testStop=false,dataOnly=false;
    int frame=-1,lastTick=-1,lastCamera=-1,smokeLimit=0,lastTestSequence=0;
    int scoutId=-1;
    std::string feedback="ready",role,testCommands;
    std::string scoutStatus="idle";
    Position home,enemyStart;
    std::map<int,Memory> remembered;
    std::vector<Unit> army,foes,bases,defenceBases;
    struct BaseWatch { int number=0,lastThreat=-10000,lastAlert=-10000;bool complete=false,underAttack=false;double pressure=0;bool airThreat=false; };
    std::map<int,BaseWatch> baseWatch;
    int expansionSerial=0,noticeUntil=-1,lastHudCommand=-1000;
    std::wstring baseNotice;
    Plans::Plan orders;
    struct FlightUnit {
        Flight::Window fire;
        bool atBase=false;
        int lastPlan=-1000;
        Position moveGoal=Positions::None;
        int nearby=-1;
        bool hold=false,resting=false;
        CommanderDefence::Guard guard;
        bool intercepting=false;
        int defendedAsset=-1;
        std::string rallyRole,rallyStage;
        double rallyRisk=0,rallyPeak=0,rallyPathAdvance=0;
        int rallySlot=-1;
        CommanderAssembly::Progress assemblyProgress;
    };
    struct CloakPause {
        int enemy=-1,lastSeen=-10000,lastPlan=-10000,safeTarget=-1;
        Position threat=Positions::None,goal=Positions::None;
        bool evading=false;
    };
    struct Escort {int task=0;Position goal=Positions::None;};
    std::map<int,Escort> escorts;
    std::set<int> destroyedEnemies;
    struct BattleTask {
        int id=0,key=0,lastPlan=-1000,clearSince=-1;
        Plans::Scope scope=Plans::Scope::All;
        Plans::Action action=Plans::Action::Gather;
        Position goal=Positions::None;
        std::map<int,CloakPause> cloakPauses;
        std::map<int,Position> escapes;
        std::map<int,FlightUnit> flight;
        std::map<int,Targeting::Engagement> targeting;
        std::map<int,Targeting::Candidate> targetFacts;
        Targeting::FocusBoard focus;
        std::map<int,Targeting::Focus> focusChoices;
        std::map<int,std::vector<Targeting::Candidate>> candidateCache;
        CommanderRaid::Objective raid;
        Position raidGoal=Positions::None;
        TilePosition raidTile=TilePositions::None;
        UnitType raidType=UnitTypes::None;
        bool mainArea=false;
        int mainTarget=-1,mainEmptySince=-1,mainUnchecked=0,mainPlanFrame=-1000;
        std::string mainStage="main_advancing";
        std::map<int,Memory> mainKnown;
        std::vector<Rules::Point> mainOtherBases;
        std::vector<Position> mainSurvey;
        std::vector<bool> mainChecked;
        std::set<int> assaultMembers,assaultJoining;
        Position assaultJoinPoint=Positions::None;
        bool clearTask=false;
        bool standbyInPlace=false;
        CommanderClear::Encounter encounter;
        int standbyPlan=-1000;
        Position standbyOrigin=Positions::None,standbyBase=Positions::None,standbyFacing=Positions::None;
        bool standbyNearBase=false;
        std::map<int,Position> standbySlots;
        std::map<int,Standby::Guard> standbyGuards;
        Position guardPost=Positions::None;
        int guardVictim=-1,lastGuardPlan=-1000;
        bool rendezvous=false;
        bool stationary=false;
        Position assemblyOrigin=Positions::None;
        Position assemblyRefuge=Positions::None;
        int assemblyRefugePlan=-10000,assemblySafeSince=-1;
        std::map<int,Position> assemblyPosts;
        Position rallyFront=Positions::None;
        std::set<int> rallyInitial,rallyFrontIds;
        int rallySerial=0;
        std::string phase="active";
        MultiBase::Defence baseDefence;
        std::map<int,BattleTask> defenceRegions;
        int regionalBase=-1,lastBasePlan=-1000,lastBaseUnitCount=-1,reportedArrived=0,reportedEngaged=0;
    };
    std::map<int,BattleTask> battleTasks;
    std::map<int,int> battleLastOrder;
    int battleOverrides=0;
    std::map<int,int> autoSince;
    std::map<int,UnitCommand> autoBefore;
    std::set<int> autoObserved;
    CommanderHud::Raster hud;
    int hudLastKey=0,hudCommandUnits=0,hudReserves=0;
    bool hudLastSafeRally=false;
    char hudPhysicalKey=0;
    Plans::Scope hudLastMode=Plans::Scope::All;
    std::ofstream log;
    std::ofstream diagnosticLog;
    void diagnosticStage(const char* stage) {
        if(!diagnosticLog.is_open())return;
        diagnosticLog<<"{\"event\":\"delegate_diagnostic\",\"stage\":\""<<stage<<"\",\"frame\":"<<game->getFrameCount()
                     <<",\"tick_ms\":"<<GetTickCount64()<<",\"pid\":"<<GetCurrentProcessId()<<",\"thread\":"<<GetCurrentThreadId()<<"}"<<std::endl;
    }
    std::ofstream cameraLog;
    Position recordedCamera = Positions::None;
    // This overlay owns only the selected scouting Probe, never the economy or
    // baseline's scout pointer. home_safe blocks resuming the old unsafe trip.
    std::map<int,int> observedScouts;
    int evadeId=-1, evadeSince=0, evadeOverrides=0, evadeLastOrder=-1;
    bool evadeHome=false;
    std::string evadeStatus="idle";
    Position evadeOrigin=Positions::None, evadeGoal=Positions::None;
    int evadeSettleUntil=-1,evadeLastPlan=-1000;
    CommanderScout::Progress evadeProgress;
    bool miningLocked=false,delegateRunning=false;
    int lastRescuedProbe=-1;
    struct MiningBuild {TilePosition tile=TilePositions::None;int until=-1;};
    std::map<int,MiningBuild> miningBuilds;
    std::map<int,int> miningResources,miningSettle;
    int miningBlocked=0;

    void recordCamera(int atFrame, bool force=false) {
        if (!cameraLog.is_open()) return;
        Position position = game->getScreenPosition();
        if (!position.isValid()) {
            // BWAPI hides getScreenPosition unless UserInput is enabled. Read
            // the native 1.16.1 camera fields documented by BWAPI instead of
            // changing the frozen bot's input permissions.
            position = Position(*reinterpret_cast<volatile std::uint32_t*>(0x00628448),
                                *reinterpret_cast<volatile std::uint32_t*>(0x00628470));
        }
        if (!position.isValid() || (!force && position == recordedCamera)) return;
        recordedCamera = position;
        cameraLog << "{\"event\":\"camera\",\"frame\":" << atFrame
                  << ",\"x\":" << position.x << ",\"y\":" << position.y << "}\n";
        if (atFrame % 120 == 0 || force) cameraLog.flush();
    }

    Rules::Point point(Position p) const { return {double(p.x),double(p.y)}; }
    Position clamp(Position p) const {
        return Position(std::clamp(p.x,16,game->mapWidth()*32-17),std::clamp(p.y,16,game->mapHeight()*32-17));
    }
    Position toward(Position a, Position b, double distance) const {
        double d = a.getDistance(b);
        return d < 1 ? a : clamp(Position(int(a.x+(b.x-a.x)*distance/d),int(a.y+(b.y-a.y)*distance/d)));
    }
    bool live(Unit u) const { return u && u->exists() && u->getHitPoints() > 0; }
    Unit ours(int id) const { Unit u = id < 0 ? nullptr : game->getUnit(id); return live(u) && u->getPlayer()==game->self() ? u : nullptr; }
    Unit enemy(int id) const {
        for (auto u : foes) if (u->getID()==id) return u;
        return nullptr;
    }
    std::vector<Unit> mainArmy() const {
        std::vector<Unit> result;
        for(auto u:army)if(live(u) && !orders.squad.count(u->getID()))result.push_back(u);
        return result;
    }
    Position groupCenter(const std::vector<Unit>& units) const {
        if(units.empty())return home;
        Unit core=units.front();size_t count=0;
        for(auto a:units) {
            size_t nearby=std::count_if(units.begin(),units.end(),[&](Unit b){return a->getDistance(b)<384;});
            if(nearby>count){count=nearby;core=a;}
        }
        Position sum(0,0);int n=0;
        for(auto u:units)if(u->getDistance(core)<384){sum+=u->getPosition();++n;}
        return Position(sum.x/std::max(1,n),sum.y/std::max(1,n));
    }
    Position center() const { return groupCenter(mainArmy()); }
    bool groundPoint(Position p, const std::vector<Unit>& units) const {
        if (!p.isValid() || !game->isWalkable(WalkPosition(p))) return false;
        return std::all_of(units.begin(),units.end(),[&](Unit u){return u->isFlying() || u->hasPath(p);});
    }
    Position reachablePoint(Position desired,const std::vector<Unit>& units) const {
        desired=clamp(desired);
        if(groundPoint(desired,units))return desired;
        for(int r=32;r<=192;r+=32)for(int i=0;i<16;++i) {
            double a=i*3.141592653589793/8;
            Position p=clamp(desired+Position(int(std::cos(a)*r),int(std::sin(a)*r)));
            if(groundPoint(p,units))return p;
        }
        return units.empty()?home:units.front()->getPosition();
    }
    double health(Unit u) const {
        auto t=u->getType(); return double(u->getHitPoints()+u->getShields())/std::max(1,t.maxHitPoints()+t.maxShields());
    }
    bool armed(Unit u) const { return u->getType().canAttack() || u->getType()==Protoss_Reaver; }
    bool realThreat(Unit u) const { return Rules::combatThreat(u->getType().isWorker(),armed(u)); }
    double strength(Unit u) const {
        if (!u->isCompleted() && !u->getType().isWorker()) return .2;
        double value=u->getType().isWorker()?.3:u->getType()==Protoss_Reaver?7:
                     u->getType().isBuilding()?4:u->getType()==Protoss_Dragoon?3:2;
        return value*std::max(.2,health(u));
    }
    std::vector<Rules::Threat> threats(bool armyTask=false) const {
        std::vector<Rules::Threat> result;
        for (auto u : foes) if (armed(u) && u->getType().groundWeapon()!=WeaponTypes::None) {
            if(armyTask && u->getType().isWorker())continue;
            int reach=u->getType().groundWeapon().maxRange();
            result.push_back({point(u->getPosition()),double(reach),strength(u),u->getType().isWorker(),
                              u->isAttacking() || u->isConstructing(),u->isDetected()});
        }
        return result;
    }
    void refresh() {
        army.clear(); foes.clear(); bases.clear();defenceBases.clear();
        for (auto u : game->self()->getUnits()) if (live(u)) {
            if(u->getType().isResourceDepot())defenceBases.push_back(u);
            if(!u->isCompleted())continue;
            if (u->getType().isResourceDepot()) bases.push_back(u);
            if (!u->getType().isWorker() && !u->getType().isBuilding() && armed(u)) army.push_back(u);
        }
        // HP is intentionally unavailable (zero) for an undetected cloaked
        // enemy. Do not mistake that partial visibility for a dead unit.
        for (auto player : game->enemies()) for (auto u : player->getUnits()) if (u &&
            CommanderDetection::observable(u->exists(),u->isVisible(),u->isDetected(),u->getHitPoints())) {
            foes.push_back(u);
            if (u->getType().isBuilding()) remembered[u->getID()]={u->getPosition(),u->getType(),u->getTilePosition()};
        }
        for (auto it=remembered.begin();it!=remembered.end();) {
            if (game->isVisible(TilePosition(it->second.position)) && !enemy(it->first)) it=remembered.erase(it);
            else ++it;
        }
        std::set<int> alive;for(auto u:army)alive.insert(u->getID());
        orders.prune(alive);
    }
    Position defendedBase() const {
        Position result=home; double best=-1e18;
        for (auto base : bases) {
            double score=-base->getDistance(enemyStart)*.001;
            BattleTask region;region.goal=base->getPosition();auto assets=protectedAssets(region);
            for(auto e:foes)if(live(e) && e->isVisible() && (armed(e) || combatEnemy(e))) {
                bool attacking=attacksAsset(e,assets);
                if(e->getType().isWorker())continue;
                if(e->getPosition().getDistance(region.goal)>CommanderDefence::zoneRadius)continue;
                score+=(attacking?1000000:0)+strength(e)*std::max(0.0,384.0-assetDistance(e,assets))*10;
            }
            if (score>best) {best=score;result=base->getPosition();}
        }
        return result;
    }
    int expansionTarget() const {
        int nearby=-1;double nearest=1e18;
        for(const auto& entry:remembered) {
            if(entry.second.type.isResourceDepot() && entry.second.position.getDistance(enemyStart)<640)continue;
            double baseDistance=1e18;
            for(auto b:defenceBases)baseDistance=std::min(baseDistance,b->getPosition().getDistance(entry.second.position));
            if(baseDistance>640)continue;
            double score=baseDistance+(entry.second.type.canAttack()?-256:0);
            if(score<nearest){nearest=score;nearby=entry.first;}
        }
        if(nearby>=0)return nearby;
        int result=-1; double best=1e18;
        for (const auto& entry : remembered) if (entry.second.type.isResourceDepot() && entry.second.position.getDistance(enemyStart)>640) {
            double d=entry.second.position.getDistance(center());
            if (d<best) {best=d;result=entry.first;}
        }
        return result;
    }
    int mainBuildingTarget() const {
        int result=-1;double nearest=512;
        for(const auto& entry:remembered)if(entry.second.type.isResourceDepot()) {
            double d=entry.second.position.getDistance(enemyStart);
            if(d<nearest){nearest=d;result=entry.first;}
        }
        return result;
    }
    void event(const char* name, const std::string& reason) {
        log<<"{\"event\":\""<<name<<"\",\"frame\":"<<frame<<",\"reason\":\""<<reason<<"\"}"<<std::endl;
    }
    static bool miningFilter(void* context,Unit worker,const UnitCommand& command) {
        return static_cast<CommanderLayer*>(context)->allowMiningCommand(worker,command);
    }
    bool allowMiningCommand(Unit worker,const UnitCommand& command) {
        if(!miningLocked || !delegateRunning)return true;
        auto type=command.getType();bool allowed=false;
        if(type==UnitCommandTypes::Build) {
            miningBuilds[worker->getID()]={command.getTargetTilePosition(),frame+48};allowed=true;
        } else if(type==UnitCommandTypes::Move) {
            auto p=command.getTargetPosition();
            allowed=CommanderMining::builderApproach(p.x,p.y,p.getDistance(enemyStart));
            if(allowed)miningBuilds[worker->getID()]={TilePosition(p.x/32-1,p.y/32-1),frame+48};
        } else if(type==UnitCommandTypes::Gather || type==UnitCommandTypes::Return_Cargo || type==UnitCommandTypes::Stop)allowed=true;
        else if(type==UnitCommandTypes::Right_Click_Unit) {
            auto target=command.getTarget();allowed=live(target) &&
                (target->getType().isMineralField() || (target->getPlayer()==game->self() && target->getType().isRefinery()));
        }
        if(allowed && command.getTarget() && command.getTarget()->getType().isResourceContainer())
            miningResources[worker->getID()]=command.getTarget()->getID();
        if(!allowed) {
            ++miningBlocked;
            log<<"{\"event\":\"mining_blocked\",\"frame\":"<<frame<<",\"probe_id\":"<<worker->getID()
               <<",\"command_type\":"<<type.getID()<<",\"target_id\":"<<(command.getTarget()?command.getTarget()->getID():-1)
               <<",\"position\":["<<worker->getPosition().x<<","<<worker->getPosition().y
               <<"],\"policy\":\"economy_lock_v1\"}"<<std::endl;
        }
        return allowed;
    }
    bool attachMiningGuards() {
        if(!miningLocked)return true;
        for(auto u:game->self()->getUnits())if(live(u) && u->isCompleted() && u->getType()==Protoss_Probe)
            if(!ProbeCommandGuard::attach(u,this,miningFilter))return false;
        return true;
    }
    bool miningBuilder(Unit u) {
        if(u->isConstructing() || u->getBuildType()!=UnitTypes::None)return true;
        auto found=miningBuilds.find(u->getID());if(found==miningBuilds.end())return false;
        bool started=false;
        for(auto b:game->self()->getUnits())if(live(b) && b->getType().isBuilding() && b->getTilePosition()==found->second.tile)started=true;
        if(CommanderMining::preserveBuild(frame,found->second.until,started,false))return true;
        miningBuilds.erase(found);return false;
    }
    void startMining(Plans::Result& result) {
        miningLocked=true;
        if(!attachMiningGuards()) {
            miningLocked=false;ProbeCommandGuard::releaseAll(this);feedback="mining_unavailable";return;
        }
        if(evadeId>=0)lastRescuedProbe=evadeId;
        evadeId=-1;evadeStatus="idle";evadeHome=false;
        for(auto u:game->self()->getUnits())if(live(u) && u->getType()==Protoss_Probe && u->isCompleted()) {
            result.units.insert(u->getID());miningSettle[u->getID()]=frame+8;
            auto cmd=u->getLastCommand();
            if(cmd.getType()==UnitCommandTypes::Gather && live(cmd.getTarget()))miningResources[u->getID()]=cmd.getTarget()->getID();
            if(cmd.getType()==UnitCommandTypes::Build)miningBuilds[u->getID()]={cmd.getTargetTilePosition(),frame+48};
            if(cmd.getType()==UnitCommandTypes::Move) {
                auto p=cmd.getTargetPosition();
                if(CommanderMining::builderApproach(p.x,p.y,p.getDistance(enemyStart)))
                    miningBuilds[u->getID()]={TilePosition(p.x/32-1,p.y/32-1),frame+48};
            }
        }
        result.accepted=true;result.status="mining_locked";feedback=result.status;
    }
    void controlMining() {
        if(!miningLocked)return;
        for(auto u:game->self()->getUnits())if(live(u) && u->isCompleted() && u->getType()==Protoss_Probe) {
            if(u->getID()==evadeId && evadeStatus!="home_safe")continue;
            bool builder=miningBuilder(u);
            bool economic=u->isGatheringMinerals() || u->isGatheringGas() || u->isCarryingMinerals() || u->isCarryingGas();
            bool attacking=u->isAttacking() || u->getOrder()==Orders::AttackUnit || u->getOrder()==Orders::AttackMove;
            auto cmd=u->getLastCommand();
            if(!builder && (attacking || !economic || frame<=miningSettle[u->getID()])) {
                Unit resource=ours(miningResources[u->getID()]);
                // Mineral fields are neutral, so resolve both resource kinds.
                if(!resource)resource=game->getUnit(miningResources[u->getID()]);
                if(!live(resource) || !resource->getType().isResourceContainer() || resource->getResources()<=0)resource=nullptr;
                if(!resource) {
                    double best=1e18;
                    for(auto m:game->getMinerals())if(m->getResources()>0 &&
                        std::any_of(bases.begin(),bases.end(),[&](Unit b){return b->getDistance(m)<380;})) {
                        double d=u->getDistance(m);if(d<best){best=d;resource=m;}
                    }
                }
                bool accepted=u->isCarryingMinerals() || u->isCarryingGas()?u->returnCargo():resource?u->gather(resource):u->holdPosition();
                if(resource)miningResources[u->getID()]=resource->getID();
                log<<"{\"event\":\"mining_order\",\"frame\":"<<frame<<",\"probe_id\":"<<u->getID()
                   <<",\"accepted\":"<<(accepted?"true":"false")<<",\"resource_id\":"<<(resource?resource->getID():-1)
                   <<",\"carrying\":"<<(u->isCarryingMinerals() || u->isCarryingGas()?"true":"false")<<"}"<<std::endl;
            }
            if(frame%120==0) {
                auto target=u->getOrderTarget();
                log<<"{\"event\":\"mining_sample\",\"frame\":"<<frame<<",\"probe_id\":"<<u->getID()
                   <<",\"position\":["<<u->getPosition().x<<","<<u->getPosition().y<<"],\"builder_exempt\":"<<(builder?"true":"false")
                   <<",\"economic\":"<<(economic?"true":"false")<<",\"native_order\":"<<u->getOrder().getID()
                   <<",\"attacking\":"<<(attacking?"true":"false")<<",\"enemy_target\":"<<(live(target) && game->self()->isEnemy(target->getPlayer())?"true":"false")
                   <<",\"resource_id\":"<<miningResources[u->getID()]<<"}"<<std::endl;
            }
        }
    }
    bool scoutProbe(Unit u) const {
        auto cmd=u->getLastCommand();
        return Rules::scoutProbe(u->getType()==Protoss_Probe,u->isCompleted(),
            u->isConstructing() || u->getBuildType()!=UnitTypes::None,
            u->isCarryingMinerals() || u->isCarryingGas(),u->getID()==scoutId,
            cmd.getType()==UnitCommandTypes::Move,cmd.getTargetPosition().getDistance(enemyStart));
    }
    void observeScouts() {
        for(auto u:game->self()->getUnits()) if(live(u) && scoutProbe(u)) {
            if(!observedScouts.count(u->getID()))event("scout_probe_seen",std::to_string(u->getID()));
            observedScouts[u->getID()]=frame;
            if(frame%120==0) {
                auto p=u->getPosition();
                log<<"{\"event\":\"scout_probe_sample\",\"frame\":"<<frame<<",\"probe_id\":"<<u->getID()
                   <<",\"position\":["<<p.x<<","<<p.y<<"],\"enemy_distance\":"<<u->getDistance(enemyStart)
                   <<",\"health\":"<<health(u)<<",\"threat_distance\":"<<(probeThreat(u)?u->getDistance(probeThreat(u)):-1)<<"}"<<std::endl;
            }
        }
        for(auto it=observedScouts.begin();it!=observedScouts.end();) {
            auto u=ours(it->first);
            if(!u || frame-it->second>240 || u->isGatheringMinerals() || u->isGatheringGas() ||
                u->isConstructing() || u->getBuildType()!=UnitTypes::None)it=observedScouts.erase(it);
            else ++it;
        }
    }
    Unit probeThreat(Unit probe, int* count=nullptr) const {
        Unit nearest=nullptr;int n=0;
        for(auto e:foes) if(armed(e) && (!e->getType().isWorker() || e->isAttacking() || e->isStartingAttack() ||
            (e->getOrder()==Orders::AttackUnit && live(e->getOrderTarget()) && e->getOrderTarget()->getPlayer()==game->self())) &&
            probe->getDistance(e)<std::max(320,e->getType().groundWeapon().maxRange()+96)) {
            ++n;if(!nearest || probe->getDistance(e)<probe->getDistance(nearest))nearest=e;
        }
        if(count)*count=n;return nearest;
    }
    Position probeEscape(Unit probe) const {
        Unit threat=probeThreat(probe);
        Position origin=probe->getPosition(),away=threat?threat->getPosition():evadeOrigin;
        if(origin.getDistance(away)<32)away=enemyStart;
        Position preferred=toward(origin,away,-320),best=Positions::None;
        double score=1e18;auto risks=threats();
        for(int i=0;i<16;++i) {
            double a=i*3.141592653589793/8;
            Position p=clamp(origin+Position(int(std::cos(a)*320),int(std::sin(a)*320)));
            if(!groundPoint(p,{probe}))continue;
            double cost=Rules::danger(point(p),risks)*3000+p.getDistance(preferred)+p.getDistance(home)*.15;
            if(cost<score){score=cost;best=p;}
        }
        return best==Positions::None?home:best;
    }
    Position probeDiversion(Unit probe) const {
        Position best=Positions::None;double score=1e18;auto risks=threats();
        for(const auto& area:BWEM::Map::Instance().Areas())for(const auto& base:area.Bases()) {
            Position p=Position(base.Location())+Position(64,48);
            if(p.getDistance(enemyStart)<800 || p.getDistance(evadeOrigin)<640 ||
                p.getDistance(home)<480 || !groundPoint(p,{probe}) || Rules::danger(point(p),risks)>.05)continue;
            if(std::any_of(remembered.begin(),remembered.end(),[&](const auto& e){return e.second.position.getDistance(p)<640;}))continue;
            double cost=probe->getDistance(p)+(game->isExplored(base.Location())?2000:0);
            if(cost<score){score=cost;best=p;}
        }
        return best;
    }
    void evadeEvent(const char* name, Unit u, bool accepted=true, bool overridden=false, Position prior=Positions::None) {
        auto cmd=u->getLastCommand();auto p=u->getPosition(),target=cmd.getTargetPosition();
        log<<"{\"event\":\""<<name<<"\",\"frame\":"<<frame<<",\"probe_id\":"<<u->getID()
           <<",\"unit_type\":"<<u->getType().getID()<<",\"phase\":\""<<evadeStatus<<"\",\"position\":["<<p.x<<","<<p.y
           <<"],\"goal\":["<<evadeGoal.x<<","<<evadeGoal.y<<"],\"last_target\":["<<target.x<<","<<target.y
           <<"],\"previous_target\":["<<prior.x<<","<<prior.y<<"],\"move\":"<<(cmd.getType()==UnitCommandTypes::Move?"true":"false")
           <<",\"accepted\":"<<(accepted?"true":"false")<<",\"overridden\":"<<(overridden?"true":"false")
           <<",\"overrides\":"<<evadeOverrides<<",\"health\":"<<health(u)
           <<",\"ownership_policy\":\"pre_delegate_probe_v1\",\"native_order\":"<<u->getOrder().getID()
           <<",\"native_target\":["<<u->getOrderTargetPosition().x<<","<<u->getOrderTargetPosition().y
           <<"],\"velocity\":["<<u->getVelocityX()<<","<<u->getVelocityY()
           <<"],\"from_start\":"<<u->getDistance(evadeOrigin)<<",\"progress_frame\":"<<evadeProgress.since
           <<",\"threat_distance\":"<<(probeThreat(u)?u->getDistance(probeThreat(u)):-1)
           <<",\"gathering\":"<<(u->isGatheringMinerals() || u->isGatheringGas()?"true":"false")
           <<",\"home_distance\":"<<u->getDistance(home)
           <<",\"latency_frames\":"<<game->getLatencyFrames()<<"}"<<std::endl;
    }
    void startEvade() {
        Unit probe=ours(evadeId);
        if(probe && evadeStatus!="home_safe" && !probe->isConstructing()) {
            // Repeated 1 confirms the existing rescue; it must not restart its
            // origin, timeout, safe route or already-latched decision to go home.
            feedback="scout_evade_applied";evadeEvent("scout_evade_reused",probe);return;
        }
        if(!probe || evadeStatus=="home_safe") {
            probe=nullptr;double nearest=1e18;
            for(const auto& entry:observedScouts)if(auto u=ours(entry.first)) {
                // Recently identified scouts remain eligible while the baseline
                // itself makes a short dodge away from the enemy base.
                if(u->getType()!=Protoss_Probe || !u->isCompleted() || u->isConstructing() ||
                    u->getBuildType()!=UnitTypes::None || u->getLastCommand().getType()!=UnitCommandTypes::Move)continue;
                double score=u->getDistance(enemyStart);
                if(score<nearest){nearest=score;probe=u;}
            }
        }
        if(!probe && miningLocked) {
            auto previous=ours(lastRescuedProbe);
            if(previous && probeThreat(previous) && !miningBuilder(previous))probe=previous;
            if(!probe)for(auto u:game->self()->getUnits())if(live(u) && u->getType()==Protoss_Probe &&
                u->isCompleted() && !miningBuilder(u) && probeThreat(u) && (!probe || health(u)<health(probe)))probe=u;
        }
        if(!probe || probe->getType()!=Protoss_Probe || probe->isConstructing()) {
            feedback="no_scout_probe";return;
        }
        // Remove the adapter slot; before-delegate renewal below also prevents
        // the frozen bot's private scout pointer from issuing the old route.
        evadeId=probe->getID();evadeSince=frame;evadeOrigin=probe->getPosition();
        lastRescuedProbe=evadeId;
        evadeLastOrder=-1;evadeLastPlan=frame;
        evadeSettleUntil=frame+std::max(8,game->getLatencyFrames()+1);
        evadeProgress.reset(frame,point(probe->getPosition()));
        if(scoutId==evadeId){scoutId=-1;scoutStatus="idle";}
        int count=0;probeThreat(probe,&count);
        bool baseThreatened=std::any_of(foes.begin(),foes.end(),[&](Unit e){return realThreat(e) && e->getDistance(home)<640;});
        evadeHome=Rules::rescueHome(health(probe),count,baseThreatened);
        evadeStatus="evading";evadeGoal=probeEscape(probe);feedback="scout_evade_applied";
        evadeEvent("scout_evade_start",probe);
    }
    void renewScoutOwnership() {
        if(evadeId<0 || evadeStatus=="home_safe")return;
        auto probe=ours(evadeId);if(!probe || !evadeGoal.isValid())return;
        auto cmd=probe->getLastCommand();
        bool matches=cmd.getType()==UnitCommandTypes::Move && cmd.getTargetPosition().getDistance(evadeGoal)<=24;
        if(!CommanderScout::renew(frame,evadeLastOrder,evadeSettleUntil,matches))return;
        bool ok=probe->move(evadeGoal);
        if(ok)evadeLastOrder=frame;
        evadeEvent("scout_evade_ownership",probe,ok,!matches,cmd.getTargetPosition());
    }
    void protectScout() {
        if(evadeId<0)return;
        Unit probe=ours(evadeId);
        if(!probe){evadeStatus="lost";event("scout_evade_lost",std::to_string(evadeId));evadeId=-1;return;}
        if(probe->getType()!=Protoss_Probe){evadeId=-1;return;}
        auto cmd=probe->getLastCommand();
        if(evadeStatus=="home_safe") {
            // Allow mining/building. Only veto this Probe's old enemy-bound
            // scouting order; the baseline may retain its private scout pointer.
            Position target=cmd.getTargetPosition();
            if(cmd.getType()==UnitCommandTypes::Move && (target.getDistance(enemyStart)<512 ||
                (evadeOrigin.getDistance(home)>640 && target.getDistance(evadeOrigin)<384))) {
                Unit mineral=nullptr;double nearest=1e18;
                for(auto m:game->getMinerals())if(m->getResources()>0 && m->getDistance(home)<384 && probe->getDistance(m)<nearest){nearest=probe->getDistance(m);mineral=m;}
                bool ok=mineral?probe->gather(mineral):probe->move(home);++evadeOverrides;
                evadeEvent("scout_evade_home_guard",probe,ok,true,target);
            }
            if(frame%24==0)evadeEvent("scout_evade_sample",probe);
            return;
        }
        std::string previous=evadeStatus;
        Unit danger=probeThreat(probe);
        if(health(probe)<.65 || frame-evadeSince>1440)evadeHome=true;
        if(evadeStatus=="evading") {
            if(!danger && (probe->getDistance(evadeOrigin)>384 || probe->getDistance(evadeGoal)<80)) {
                evadeGoal=evadeHome?Positions::None:probeDiversion(probe);
                evadeStatus=evadeGoal==Positions::None?"returning":"diverting";
                if(evadeStatus=="returning")evadeGoal=home;
            } else if(probe->getDistance(evadeGoal)<80 ||
                (danger && frame-evadeLastPlan>=24 && Rules::danger(point(evadeGoal),threats())>.2)) {
                evadeGoal=probeEscape(probe);evadeLastPlan=frame;
            }
        } else if(danger) {
            evadeHome=true;evadeStatus="evading";evadeGoal=probeEscape(probe);
        } else if(evadeStatus=="diverting" && (evadeHome || probe->getDistance(evadeGoal)<112)) {
            evadeStatus="returning";evadeGoal=home;
        }
        if(evadeStatus=="returning" && probe->getDistance(home)<180 && !danger) {
            evadeStatus="home_safe";evadeGoal=home;
            Unit mineral=nullptr;for(auto m:game->getMinerals())if(m->getResources()>0 && m->getDistance(home)<320){mineral=m;break;}
            bool ok=mineral?probe->gather(mineral):probe->stop();
            evadeEvent("scout_evade_state",probe,ok);return;
        }
        if(!groundPoint(evadeGoal,{probe})) {evadeHome=true;evadeStatus="returning";evadeGoal=home;}
        if(evadeProgress.stalled(frame,point(probe->getPosition()),probe->getDistance(evadeGoal))) {
            // A cached accepted move is insufficient proof of progress. Try a
            // fresh local escape or a reachable return point after a real stall.
            evadeHome=true;
            evadeGoal=danger?probeEscape(probe):reachablePoint(home,{probe});
            evadeStatus=danger?"evading":"returning";evadeLastPlan=frame;
            evadeProgress.reset(frame,point(probe->getPosition()));
            evadeSettleUntil=frame+std::max(8,game->getLatencyFrames()+1);
            evadeEvent("scout_evade_stall_replan",probe);
        }
        bool needed=Rules::rescueMoveNeeded(cmd.getType()==UnitCommandTypes::Move,cmd.getTargetPosition().getDistance(evadeGoal),probe->isIdle());
        if(needed) {
            bool overridden=evadeLastOrder>=0 && probe->getLastCommandFrame()>evadeLastOrder;
            if(overridden)++evadeOverrides;
            bool ok=probe->move(evadeGoal);if(ok)evadeLastOrder=frame;
            evadeEvent("scout_evade_order",probe,ok,overridden,cmd.getTargetPosition());
        }
        if(previous!=evadeStatus)evadeEvent("scout_evade_state",probe);
        if(frame%24==0)evadeEvent("scout_evade_sample",probe);
    }
    void writeIds(const std::set<int>& ids) {
        log<<"[";bool comma=false;for(int id:ids){if(comma)log<<",";log<<id;comma=true;}log<<"]";
    }
    const char* taskLabel(int id) const {
        auto it=battleTasks.find(id);return it==battleTasks.end()?"baseline":Plans::actionName(it->second.action);
    }
    std::vector<Unit> taskUnits(int id) const {
        std::vector<Unit> result;
        for(auto u:army)if(live(u) && orders.taskFor(u->getID())==id)result.push_back(u);
        return result;
    }
    Unit closeThreat(Unit u,double radius=400) const {
        Unit best=nullptr;
        for(auto e:foes)if(armed(e) && e->getType().groundWeapon()!=WeaponTypes::None &&
            !e->getType().isWorker() && u->getDistance(e)<radius &&
            (!best || u->getDistance(e)<u->getDistance(best)))best=e;
        return best;
    }
    Position safeGather(const std::vector<Unit>& units) const {
        Position origin=groupCenter(units),best=reachablePoint(origin,units);
        auto risks=threats(true);double score=1e18;
        auto consider=[&](Position p) {
            p=clamp(p);if(!groundPoint(p,units))return;
            double cost=Rules::danger(point(p),risks)*5000+p.getDistance(origin)+p.getDistance(home)*.08;
            if(cost<score){score=cost;best=p;}
        };
        consider(origin);consider(toward(origin,home,std::min(320.0,origin.getDistance(home))));
        for(int r:{160,320,480})for(int i=0;i<16;++i) {
            double a=i*3.141592653589793/8;
            consider(origin+Position(int(std::cos(a)*r),int(std::sin(a)*r)));
        }
        return best;
    }
    Position safeEscape(Unit u,Position previous=Positions::None) const {
        Position origin=u->getPosition();Unit threat=closeThreat(u,800);
        Position preferred=threat?toward(origin,threat->getPosition(),-384):
            toward(origin,home,std::min(384.0,origin.getDistance(home)));
        Position best=reachablePoint(preferred,{u});double score=1e18;auto risks=threats(true);
        auto consider=[&](Position p) {
            p=clamp(p);if(!groundPoint(p,{u}))return;
            double reverse=0;
            if(previous.isValid() && origin.getDistance(previous)>96) {
                double ax=previous.x-origin.x,ay=previous.y-origin.y,bx=p.x-origin.x,by=p.y-origin.y;
                reverse=std::max(0.0,-(ax*bx+ay*by)/std::max(1.0,std::hypot(ax,ay)*std::hypot(bx,by)))*480;
            }
            double cost=Rules::danger(point(p),risks)*5000+p.getDistance(preferred)+p.getDistance(home)*.1+reverse;
            if(cost<score){score=cost;best=p;}
        };
        consider(preferred);
        for(int r:{256,384,512})for(int i=0;i<16;++i) {
            double a=i*3.141592653589793/8;
            consider(origin+Position(int(std::cos(a)*r),int(std::sin(a)*r)));
        }
        return best;
    }
    std::vector<Rally::Hazard> rallyHazards() const {
        std::vector<Rally::Hazard> result;
        for(auto e:foes)if(live(e) && e->isVisible() && e->isCompleted() &&
            (!e->getType().isBuilding() || !e->getType().requiresPsi() || e->isPowered())) {
            auto t=e->getType();
            if(t.isWorker())continue;
            int reach=std::max(e->getPlayer()->weaponMaxRange(t.groundWeapon()),e->getPlayer()->weaponMaxRange(t.airWeapon()));
            bool damaging=t.groundWeapon()!=WeaponTypes::None || t.airWeapon()!=WeaponTypes::None;
            if(t==Terran_Bunker || t==Protoss_Carrier || t==Protoss_Reaver){damaging=true;reach=std::max(reach,256);}
            for(auto ability:t.abilities())if(ability.getWeapon()!=WeaponTypes::None && ability.getWeapon().damageAmount()>0) {
                damaging=true;reach=std::max(reach,ability.getWeapon().maxRange());
            }
            if(damaging)result.push_back({point(e->getPosition()),double(std::max(192,reach+96)),std::max(1.0,strength(e))});
        }
        return result;
    }
    bool rallyOpen(Position p,const std::vector<Unit>& units) const {
        if(!groundPoint(p,units))return false;
        auto blocked=[&](Unit b) {
            if(!live(b) || !b->getType().isBuilding())return false;
            auto delta=p-b->getPosition();
            return std::abs(delta.x)<b->getType().width()/2+24 && std::abs(delta.y)<b->getType().height()/2+24;
        };
        for(auto b:game->self()->getUnits())if(blocked(b))return false;
        for(auto b:foes)if(blocked(b))return false;
        return true;
    }
    Position rallyFront(const std::vector<Unit>& units) const {
        if(units.empty())return home;
        std::vector<Unit> pressed;
        for(auto u:units)if(closeThreat(u,512))pressed.push_back(u);
        if(!pressed.empty())return groupCenter(pressed);
        Unit best=units.front();double score=-1e18;
        for(auto u:units) {
            int nearby=int(std::count_if(units.begin(),units.end(),[&](Unit v){return u->getDistance(v)<384;}));
            double value=Rally::progress(point(home),point(enemyStart),point(u->getPosition()))+nearby*64;
            if(value>score){score=value;best=u;}
        }
        std::vector<Unit> forward;
        for(auto u:units)if(u->getDistance(best)<384)forward.push_back(u);
        return groupCenter(forward);
    }
    Position assemblyFront(const std::vector<Unit>& units) const {
        if(units.empty())return Positions::None;
        Unit best=nullptr;double score=-1e18;
        for(auto u:units) {
            int count=int(std::count_if(units.begin(),units.end(),[&](Unit v){return u->getDistance(v)<384;}));
            if(units.size()>1 && count<2)continue;
            double value=Rally::progress(point(home),point(enemyStart),point(u->getPosition()))+count*32;
            if(value>score){best=u;score=value;}
        }
        if(!best)return groupCenter(units);
        std::vector<Unit> front;for(auto u:units)if(u->getDistance(best)<384)front.push_back(u);
        return groupCenter(front);
    }
    Position selectRally(const BattleTask& task,const std::vector<Unit>& units,const std::vector<Rally::Hazard>& hazards,bool relocate) const {
        Position origin=relocate?task.goal:task.rallyFront;
        Position preferred=toward(origin,home,std::min(relocate?256.0:384.0,origin.getDistance(home)));
        Position best=Positions::None;double score=1e18;
        double oldProgress=Rally::progress(point(home),point(task.rallyFront),point(origin));
        auto consider=[&](Position p) {
            p=clamp(p);if(!rallyOpen(p,units))return;
            if(task.raid.withdrawing && !CommanderRaid::safeWithdrawal(point(p),point(task.raidGoal),point(enemyStart),point(home)))return;
            if(task.rallyFront.getDistance(home)>384 && Rally::progress(point(home),point(task.rallyFront),point(p))>oldProgress-(relocate?64:192))return;
            double risk=Rally::exposure(point(p),hazards);
            // Keep the entire holding formation behind the threat, not merely
            // its center. The target stays fixed as reinforcements appear.
            for(const auto& h:hazards)risk+=std::max(0.0,(h.radius+224-Rules::distance(point(p),h.position))/(h.radius+224))*h.weight;
            double cost=risk*30000+p.getDistance(preferred)+std::abs(p.getDistance(origin)-(relocate?256:384))*.2;
            if(cost<score){score=cost;best=p;}
        };
        consider(preferred);
        for(int r:{192,256,384,512,640})for(int i=0;i<16;++i) {
            double a=i*3.141592653589793/8;
            consider(origin+Position(int(std::cos(a)*r),int(std::sin(a)*r)));
        }
        consider(home);
        if(best.isValid())return best;
        std::vector<Unit> buildings;
        for(auto b:game->self()->getUnits())if(live(b) && b->getType().isBuilding())buildings.push_back(b);
        return defencePoint(home,units,buildings);
    }
    void prepareRally(BattleTask& task,const std::vector<Unit>& units,const std::vector<Rally::Hazard>& hazards) {
        if(units.empty())return;
        bool initial=!task.rallyFront.isValid();
        if(initial)task.rallyFront=rallyFront(units);
        bool occupied=!rallyOpen(task.goal,{});
        bool unsafe=Rally::exposure(point(task.goal),hazards)>Rally::epsilon;
        if(initial || ((unsafe || occupied) && frame-task.lastPlan>=24)) {
            Position previous=task.goal;
            task.goal=selectRally(task,units,hazards,!initial);task.lastPlan=frame;
            log<<"{\"event\":\"rendezvous_point\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
               <<",\"reason\":\""<<(initial?"frontline_withdrawal":unsafe?"known_threat":"occupied_point")
               <<"\",\"goal\":["<<task.goal.x<<","<<task.goal.y<<"],\"previous\":["<<previous.x<<","<<previous.y
               <<"],\"front\":["<<task.rallyFront.x<<","<<task.rallyFront.y<<"],\"home\":["<<home.x<<","<<home.y<<"]}"<<std::endl;
        }
    }
    Position rallyWaypoint(Unit u,Position goal) const {
        Position from=u->getPosition();
        if(!u->isFlying()) {
            const auto& path=BWEM::Map::Instance().GetPath(from,goal);
            for(auto cp:path) {
                Position p(cp->Center());
                if(from.getDistance(p)>16)return toward(from,p,std::min(320.0,from.getDistance(p)));
            }
        }
        return toward(from,goal,std::min(320.0,from.getDistance(goal)));
    }
    double rallyPathDistance(Position from,Position to,bool flying) const {
        if(flying)return from.getDistance(to);
        int length=-1;BWEM::Map::Instance().GetPath(from,to,&length);
        return length>=0?double(length):from.getDistance(to);
    }
    void rendezvousUnit(Unit u,BattleTask& task,const std::vector<Rally::Hazard>& hazards) {
        auto& f=task.flight[u->getID()];Position from=u->getPosition();
        if(f.rallyRole.empty()) {
            f.rallySlot=task.rallySerial++;
            f.rallyRole=!task.rallyInitial.count(u->getID())?"new_unit":task.rallyFrontIds.count(u->getID())?"frontline":"reinforcement";
            log<<"{\"event\":\"rendezvous_join\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
               <<",\"unit_id\":"<<u->getID()<<",\"role\":\""<<f.rallyRole<<"\",\"position\":["<<from.x<<","<<from.y
               <<"],\"goal\":["<<task.goal.x<<","<<task.goal.y<<"]}"<<std::endl;
        }
        f.rallyRisk=Rally::exposure(point(from),hazards);f.resting=false;f.atBase=false;
        Unit pursuer=closeThreat(u,800);f.nearby=pursuer?pursuer->getID():-1;
        if(from.getDistance(task.goal)<=Rally::arrival && f.rallyRisk<=Rally::epsilon) {
            Position slot=task.goal+Position((f.rallySlot%7-3)*40,(f.rallySlot/7%7-3)*40);
            // Do not make the first arrivals hold a solid ring across the
            // reinforcement route. Spread inside the one shared rally area.
            bool settle=from.getDistance(slot)>28 && rallyOpen(slot,{u}) && Rally::safeLeg(point(from),point(slot),hazards);
            f.moveGoal=settle?slot:from;f.resting=true;f.hold=!settle;f.rallyStage="waiting";f.rallyPeak=0;f.rallyPathAdvance=0;
            return;
        }
        bool rear=f.rallyRole!="frontline";
        double routeDistance=rallyPathDistance(from,task.goal,u->isFlying());
        f.rallyPathAdvance=f.moveGoal.isValid()?routeDistance-rallyPathDistance(f.moveGoal,task.goal,u->isFlying()):0;
        bool valid=f.moveGoal.isValid() && Rally::safeLeg(point(from),point(f.moveGoal),hazards) &&
            (f.rallyRisk>Rally::epsilon || !rear || Rally::rearAdvance(point(from),point(task.goal),point(f.moveGoal)) || f.rallyPathAdvance>16);
        if(!valid || frame-f.lastPlan>=24 || from.getDistance(f.moveGoal)<24 || (u->isStuck() && frame-f.lastPlan>=24)) {
            Position waypoint=rallyWaypoint(u,task.goal);
            std::vector<Rally::Candidate> candidates;
            auto add=[&](Position p) {
                p=clamp(p);bool open=rallyOpen(p,{u});
                if(open && !u->isFlying()) {
                    int steps=std::max(1,int(from.getDistance(p)/24));
                    for(int i=1;i<=steps && open;++i) {
                        Position q=from+Position((p.x-from.x)*i/steps,(p.y-from.y)*i/steps);
                        open=game->isWalkable(WalkPosition(q));
                    }
                }
                bool advancing=routeDistance-rallyPathDistance(p,task.goal,u->isFlying())>16;
                candidates.push_back({point(p),open,advancing});
            };
            add(waypoint);if(from.getDistance(task.goal)<384)add(task.goal);
            if(f.moveGoal.isValid())add(f.moveGoal);
            for(int r:{64,128,256,384})for(int i=0;i<16;++i) {
                double a=i*3.141592653589793/8;
                add(from+Position(int(std::cos(a)*r),int(std::sin(a)*r)));
            }
            auto choice=Rally::choose(point(from),point(waypoint),point(task.goal),point(f.moveGoal),f.moveGoal.isValid(),rear,hazards,candidates);
            f.moveGoal=Position(int(choice.position.x),int(choice.position.y));f.lastPlan=frame;f.rallyPeak=choice.routePeak;
            f.rallyPathAdvance=routeDistance-rallyPathDistance(f.moveGoal,task.goal,u->isFlying());
            f.hold=!choice.moving;
        } else {f.hold=false;f.rallyPeak=Rally::peak(point(from),point(f.moveGoal),hazards);}
        f.rallyStage=f.rallyRisk>Rally::epsilon?"evading":f.hold?"route_wait":"joining";
        // Reuse the brief stationary firing budget, followed by mandatory
        // movement. No attack-move or pursuing attack-unit command is issued.
        if(f.rallyRisk>Rally::epsilon && briefFlightFire(u,task))f.hold=true;
    }
    void prepareAssembly(BattleTask& task,const std::vector<Unit>& units) {
        if(!task.assemblyOrigin.isValid()) {
            task.assemblyOrigin=groupCenter(units);task.goal=task.assemblyOrigin;
            for(auto u:units)task.rallyInitial.insert(u->getID());
            log<<"{\"event\":\"assembly_point\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
               <<",\"origin\":["<<task.goal.x<<","<<task.goal.y<<"],\"policy\":\"shared_local_refuge_v2\"}"<<std::endl;
        }
        auto world=standbyWorld();
        for(auto it=task.assemblyPosts.begin();it!=task.assemblyPosts.end();) {
            auto member=std::find_if(units.begin(),units.end(),[&](Unit u){return u->getID()==it->first;});
            if(member==units.end() || !standbyOpen(it->second,{*member},world,16))it=task.assemblyPosts.erase(it);
            else ++it;
        }
        auto ranked=units;
        std::stable_sort(ranked.begin(),ranked.end(),[&](Unit a,Unit b){
            auto front=[&](Unit u){return task.rallyInitial.count(u->getID()) && CommanderAssembly::preservePost(u->getDistance(task.assemblyOrigin));};
            return front(a)>front(b);
        });
        // Reserve all unchanged front posts before placing any arrivals.
        for(auto u:ranked)if(!task.assemblyPosts.count(u->getID())) {
            Position post=u->getPosition();bool frontline=task.rallyInitial.count(u->getID()) &&
                CommanderAssembly::preservePost(u->getDistance(task.assemblyOrigin));
            if(!frontline || !standbyOpen(post,{u},world,16)) {
                double best=1e18;Position chosen=Positions::None;
                for(int radius:{0,48,96,144,192})for(int i=0;i<(radius?16:1);++i) {
                    double angle=i*3.141592653589793/8;
                    Position p=clamp(task.assemblyOrigin+Position(int(std::cos(angle)*radius),int(std::sin(angle)*radius)));
                    if(!standbyOpen(p,{u},world,20))continue;
                    bool occupied=std::any_of(task.assemblyPosts.begin(),task.assemblyPosts.end(),[&](const auto& e){
                        return ours(e.first) && e.second.getDistance(p)<48;
                    });
                    if(occupied)continue;
                    double score=p.getDistance(task.assemblyOrigin)+p.getDistance(u->getPosition())*.05;
                    if(score<best){best=score;chosen=p;}
                }
                // No usable post: wait for space instead of moving the anchor
                // or sending the unit into a building/mineral footprint.
                if(!chosen.isValid())continue;
                post=chosen;
            }
            task.assemblyPosts[u->getID()]=post;
            auto& f=task.flight[u->getID()];
            f.rallyRole=frontline?"frontline":task.rallyInitial.count(u->getID())?"reinforcement":"new_unit";
            log<<"{\"event\":\"assembly_join\",\"frame\":"<<frame<<",\"task_id\":"<<task.id<<",\"unit_id\":"<<u->getID()
               <<",\"role\":\""<<f.rallyRole<<"\",\"position\":["<<u->getPosition().x<<","<<u->getPosition().y
               <<"],\"post\":["<<post.x<<","<<post.y<<"]}"<<std::endl;
        }
    }
    Unit assemblyEnemy(Unit u,const BattleTask& task) const {
        Position area=task.assemblyRefuge.isValid()?task.assemblyRefuge:task.assemblyOrigin;
        if(u->getDistance(area)>CommanderAssembly::fieldRadius)return nullptr;
        Unit result=nullptr;double best=1e18;
        for(auto e:foes)if(live(e) && e->isVisible() && u->canAttack(e) &&
            CommanderAssembly::returnFire(e->getType().isWorker(),e->isDetected(),combatEnemy(e) ||
                (e->getType().isBuilding() && armed(e)),u->isInWeaponRange(e))) {
            double score=u->getDistance(e)+health(e)*32;
            if(score<best){best=score;result=e;}
        }
        return result;
    }
    std::vector<Rally::Hazard> assemblyHazards(const BattleTask& task) const {
        auto hazards=rallyHazards();
        for(auto e:foes)if(e->exists() && e->isVisible() && !e->isDetected() && !e->getType().isWorker() && e->getType().groundWeapon()!=WeaponTypes::None)
            hazards.push_back({point(e->getPosition()),320,4});
        for(const auto& p:task.cloakPauses)if(frame-p.second.lastSeen<=240)
            hazards.push_back({point(p.second.threat),320,2});
        return hazards;
    }
    void planAssemblyRefuge(BattleTask& task,const std::vector<Unit>& units,const std::vector<Rally::Hazard>& hazards) {
        bool pressure=false;
        for(auto u:units)if(u->getDistance(task.assemblyOrigin)<=384) {
            Unit pursuer=closeThreat(u,448);bool outranged=false;
            if(pursuer) {
                auto weapon=u->isFlying()?pursuer->getType().airWeapon():pursuer->getType().groundWeapon();
                outranged=weapon!=WeaponTypes::None && !u->isInWeaponRange(pursuer) && u->getDistance(pursuer)<=pursuer->getPlayer()->weaponMaxRange(weapon)+64;
            }
            bool hidden=std::any_of(foes.begin(),foes.end(),[&](Unit e){return e->exists() && e->isVisible() && !e->isDetected() &&
                !e->getType().isWorker() && e->getType().groundWeapon()!=WeaponTypes::None && e->getDistance(u)<320;});
            pressure|=hidden || (!(assemblyEnemy(u,task) && health(u)>=.4) && CommanderAssembly::localEscape(Rally::exposure(point(u->getPosition()),hazards),u->isUnderAttack(),health(u)<.35,outranged));
        }
        bool originSafe=Rally::exposure(point(task.assemblyOrigin),hazards)<=Rally::epsilon;
        if(!pressure && originSafe) {
            if(task.assemblySafeSince<0)task.assemblySafeSince=frame;
            if(task.assemblyRefuge.isValid() && frame-task.assemblySafeSince>=120) {
                task.assemblyRefuge=Positions::None;
                log<<"{\"event\":\"assembly_refuge\",\"frame\":"<<frame<<",\"task_id\":"<<task.id<<",\"state\":\"returning_posts\"}"<<std::endl;
            }
            return;
        }
        task.assemblySafeSince=-1;
        if(!pressure && !task.assemblyRefuge.isValid())return;
        if(task.assemblyRefuge.isValid() && Rally::exposure(point(task.assemblyRefuge),hazards)<=Rally::epsilon)return;
        if(frame-task.assemblyRefugePlan<48)return;
        task.assemblyRefugePlan=frame;Position best=task.assemblyOrigin;double score=1e18;
        Position front=groupCenter(units);
        for(int radius:{0,96,192,288,384})for(int i=0;i<(radius?24:1);++i) {
            double angle=i*3.141592653589793/12;
            Position p=clamp(task.assemblyOrigin+Position(int(std::cos(angle)*radius),int(std::sin(angle)*radius)));
            if(!rallyOpen(p,units))continue;
            double value=Rally::exposure(point(p),hazards)*10000+Rally::peak(point(front),point(p),hazards)*32+
                p.getDistance(task.assemblyOrigin)+p.getDistance(front)*.2;
            if(task.assemblyRefuge.isValid())value+=p.getDistance(task.assemblyRefuge)*.1;
            if(value<score){score=value;best=p;}
        }
        bool changed=!task.assemblyRefuge.isValid() || task.assemblyRefuge.getDistance(best)>24;
        task.assemblyRefuge=best;
        if(changed)log<<"{\"event\":\"assembly_refuge\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
            <<",\"state\":\"shared_local_refuge\",\"origin\":["<<task.assemblyOrigin.x<<","<<task.assemblyOrigin.y
            <<"],\"goal\":["<<best.x<<","<<best.y<<"],\"radius\":384}"<<std::endl;
    }
    Rally::Choice assemblyStep(Unit u,const BattleTask& task,Position post,const std::vector<Rally::Hazard>& hazards) const {
        Position from=u->getPosition(),waypoint=rallyWaypoint(u,post);
        std::vector<Rally::Candidate> candidates;double before=rallyPathDistance(from,post,u->isFlying());
        auto add=[&](Position p) {
            p=clamp(p);bool advancing=before-rallyPathDistance(p,post,u->isFlying())>16;
            bool open=rallyOpen(p,{u}) && CommanderAssembly::boundedStep(from.getDistance(task.assemblyOrigin),p.getDistance(task.assemblyOrigin),advancing);
            if(open && !u->isFlying())for(int i=1,n=std::max(1,int(from.getDistance(p)/24));i<=n && open;++i)
                open=game->isWalkable(WalkPosition(from+Position((p.x-from.x)*i/n,(p.y-from.y)*i/n)));
            candidates.push_back({point(p),open,advancing});
        };
        add(waypoint);if(from.getDistance(post)<384)add(post);
        for(int radius:{64,128,192})for(int i=0;i<16;++i) {
            double a=i*3.141592653589793/8;add(from+Position(int(std::cos(a)*radius),int(std::sin(a)*radius)));
        }
        return Rally::choose(point(from),point(waypoint),point(post),point(from),false,true,hazards,candidates);
    }
    void controlAssembly(BattleTask& task,const std::vector<Unit>& units) {
        prepareAssembly(task,units);
        auto hazards=assemblyHazards(task);planAssemblyRefuge(task,units,hazards);
        int arrived=0,engaged=0,evading=0,protectedCount=0,skirmishing=0;
        auto previousPhase=task.phase;
        for(auto u:units) {
            auto& f=task.flight[u->getID()];
            if(cloakProtection(u,task)){++protectedCount;evading+=task.cloakPauses.at(u->getID()).evading;skirmishing+=task.cloakPauses.at(u->getID()).safeTarget>=0;continue;}
            auto found=task.assemblyPosts.find(u->getID());
            Position from=u->getPosition(),post=found==task.assemblyPosts.end()?from:found->second;
            if(task.assemblyRefuge.isValid()) {
                Position spread=task.assemblyRefuge+Position((u->getID()%3-1)*32,(u->getID()/3%3-1)*32);
                post=spread.getDistance(task.assemblyOrigin)<=CommanderAssembly::refugeRadius && rallyOpen(spread,{u})?spread:task.assemblyRefuge;
            }
            Unit target=assemblyEnemy(u,task),pursuer=closeThreat(u,448);
            f.rallyRisk=Rally::exposure(point(from),hazards);f.resting=false;f.nearby=pursuer?pursuer->getID():-1;
            bool outranged=false;
            if(pursuer) {
                auto weapon=u->isFlying()?pursuer->getType().airWeapon():pursuer->getType().groundWeapon();
                outranged=weapon!=WeaponTypes::None && !u->isInWeaponRange(pursuer) &&
                    u->getDistance(pursuer)<=pursuer->getPlayer()->weaponMaxRange(weapon)+64;
            }
            bool escape=CommanderAssembly::localEscape(f.rallyRisk,u->isUnderAttack(),health(u)<.35,outranged);
            if(!escape && !task.assemblyRefuge.isValid() && f.assemblyProgress.stalled(frame,point(from),point(post))) {
                Position chosen=post;double best=1e18;auto world=standbyWorld();
                for(int radius:{0,48,96,144,192})for(int i=0;i<(radius?16:1);++i) {
                    double a=i*3.141592653589793/8;
                    Position p=clamp(post+Position(int(std::cos(a)*radius),int(std::sin(a)*radius)));
                    if(p.getDistance(task.assemblyOrigin)>CommanderAssembly::fieldRadius || !standbyOpen(p,{u},world,24) ||
                       Rally::exposure(point(p),hazards)>Rally::epsilon)continue;
                    bool occupied=std::any_of(units.begin(),units.end(),[&](Unit v){return v!=u &&
                        v->getDistance(p)<u->getType().width()/2+16;});
                    if(occupied)continue;
                    double value=p.getDistance(post)+p.getDistance(from)*.2;
                    if(value<best){best=value;chosen=p;}
                }
                if(chosen!=post) {
                    log<<"{\"event\":\"assembly_post_adjusted\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
                       <<",\"unit_id\":"<<u->getID()<<",\"previous\":["<<post.x<<","<<post.y<<"],\"post\":["<<chosen.x<<","<<chosen.y
                       <<"],\"reason\":\"friendly_blockage\"}"<<std::endl;
                    task.assemblyPosts[u->getID()]=chosen;post=chosen;f.assemblyProgress.since=-1;
                }
            }
            if(target && health(u)>=.4) {
                f.moveGoal=from;f.hold=false;f.rallyStage="defending";++engaged;
            } else if(!escape && (found==task.assemblyPosts.end() || from.getDistance(post)<=CommanderAssembly::arrival)) {
                target=nullptr;f.moveGoal=from;f.hold=true;f.resting=true;f.rallyStage=found==task.assemblyPosts.end()?"route_wait":"waiting";++arrived;
            } else {
                target=nullptr;
                auto choice=assemblyStep(u,task,post,hazards);
                f.moveGoal=Position(int(choice.position.x),int(choice.position.y));f.hold=!choice.moving;f.rallyPeak=choice.routePeak;
                f.rallyStage=escape?"evading":f.hold?"route_wait":"joining";
                if(escape)++evading;
            }
            battleOrder(u,task,f.moveGoal,target,false,f.hold);
            if(frame%120==0) {
                log<<"{\"event\":\"assembly_sample\",\"frame\":"<<frame<<",\"task_id\":"<<task.id<<",\"unit_id\":"<<u->getID()
                   <<",\"position\":["<<from.x<<","<<from.y<<"],\"goal\":["<<f.moveGoal.x<<","<<f.moveGoal.y<<"]";
                writeAssembly(task,u);log<<"}"<<std::endl;
            }
        }
        task.phase=skirmishing?"detection_skirmishing":task.assemblyRefuge.isValid()?"assembly_shared_refuge":protectedCount?(evading?"evading_cloak":"waiting_detection"):evading?"assembly_evading":
            engaged?"assembly_defending":arrived?"assembly_waiting":"assembly_joining";
        task.reportedArrived=arrived;task.reportedEngaged=engaged;
        if(frame%120==0 || task.phase!=previousPhase)taskState(task,units,arrived);
    }
    void writeAssembly(const BattleTask& task,Unit u=nullptr) {
        log<<",\"assembly_policy\":\"shared_local_refuge_v2\",\"assembly_origin\":["<<task.assemblyOrigin.x<<","<<task.assemblyOrigin.y<<"]";
        if(task.assemblyRefuge.isValid())log<<",\"assembly_refuge\":["<<task.assemblyRefuge.x<<","<<task.assemblyRefuge.y<<"]";
        if(u) {
            auto post=task.assemblyPosts.find(u->getID());auto f=task.flight.find(u->getID());
            if(post!=task.assemblyPosts.end())log<<",\"assembly_post\":["<<post->second.x<<","<<post->second.y<<"]";
            if(f!=task.flight.end())log<<",\"assembly_stage\":\""<<f->second.rallyStage<<"\",\"assembly_role\":\""<<f->second.rallyRole<<"\"";
        }
    }
    void releaseUnit(Unit u,const char* kind) {
        if(!live(u))return;
        auto previous=u->getLastCommand();
        // Stop the old engine order ONCE, then relinquish all control. Merely
        // erasing the task would leave an old attack/hold/move running natively.
        bool accepted=u->stop();auto final=u->getLastCommand();
        log<<"{\"event\":\"manual_release\",\"frame\":"<<frame<<",\"unit_id\":"<<u->getID()
           <<",\"kind\":\""<<kind<<"\",\"previous_command\":"<<previous.getType().getID()
           <<",\"accepted\":"<<(accepted?"true":"false")<<",\"verified\":"<<(final.getType()==UnitCommandTypes::Stop?"true":"false")<<"}"<<std::endl;
        battleLastOrder.erase(u->getID());battleLastTask.erase(u->getID());
        autoSince[u->getID()]=frame;autoObserved.erase(u->getID());
    }
    bool combatEnemy(Unit e) const {
        auto type=e->getType();
        if(type.isWorker() || type.isBuilding())return false;
        if(armed(e) || type==Protoss_Carrier)return true;
        for(auto ability:type.abilities())if(ability.getWeapon()!=WeaponTypes::None && ability.getWeapon().damageAmount()>0)return true;
        return false;
    }
    Position patrolPost(const BattleTask& task,Unit u) const {
        auto post=task.standbySlots.find(u->getID());
        return post==task.standbySlots.end()?Position(int(task.encounter.origin.x),int(task.encounter.origin.y)):post->second;
    }
    Position clearEnemyGoal(const BattleTask& task,const std::vector<Unit>& units) const {
        Position best=Positions::None;double score=1e18;
        Position origin(int(task.encounter.origin.x),int(task.encounter.origin.y));
        for(auto e:foes)if(live(e) && e->isVisible() && e->isDetected()) {
            auto type=e->getType();auto victim=e->getOrderTarget();
            bool active=live(victim) && victim->getPlayer()==game->self() &&
                (e->isAttacking() || e->isStartingAttack() || e->getOrder()==Orders::AttackUnit);
            bool defensive=type.isBuilding() && (armed(e) || type==Terran_Bunker);
            bool stationary=type.isBuilding() || !type.canMove() || e->isBurrowed() || e->isSieged();
            bool permitted=std::any_of(units.begin(),units.end(),[&](Unit u) {
                auto delta=e->getPosition()-u->getPosition();
                bool fleeing=e->getVelocityX()*delta.x+e->getVelocityY()*delta.y>std::max(1.0,e->getPosition().getDistance(u->getPosition()))*.5;
                bool local=CommanderClear::localDefence(type.isWorker(),combatEnemy(e) || defensive,active,u->isInWeaponRange(e),fleeing,u->getDistance(e));
                return CommanderClear::target(type.isWorker(),type.isBuilding(),combatEnemy(e),active,e->getDistance(patrolPost(task,u)),defensive,local) &&
                    u->canAttack(e) && (u->isFlying() || u->hasPath(e->getPosition())) &&
                    CommanderClear::approach(stationary,u->isInWeaponRange(e),fleeing,u->getDistance(e));
            });
            if(permitted) {double d=e->getDistance(groupCenter(units));if(d<score){score=d;best=e->getPosition();}}
        }
        return best;
    }
    bool cleared(const BattleTask& task) const { return task.clearTask && task.encounter.finished; }
    Standby::World standbyWorld() const {
        Standby::World world;
        auto obstacle=[&](Unit b) {
            auto t=b->getType();world.buildings.push_back({point(b->getPosition()),t.width()/2.0+24,t.height()/2.0+24});
        };
        for(auto b:game->self()->getUnits())if(live(b) && b->getType().isBuilding()) {
            obstacle(b);
            if(b->getType().canProduce())for(Position direction:{Position(1,0),Position(-1,0),Position(0,1),Position(0,-1)}) {
                int edge=direction.x?b->getType().width()/2:b->getType().height()/2;
                world.lanes.push_back({point(b->getPosition()+direction*(edge+16)),point(b->getPosition()+direction*(edge+112)),16});
            }
        }
        for(auto b:foes)if(live(b) && b->getType().isBuilding())obstacle(b);
        auto resource=[&](Unit r) {
            obstacle(r);
            for(auto b:bases)if(live(b) && b->getDistance(r)<480)
                world.lanes.push_back({point(b->getPosition()),point(r->getPosition()),24});
        };
        for(auto m:game->getMinerals())if(live(m) && m->getResources()>0)resource(m);
        for(auto g:game->getGeysers())if(live(g))resource(g);
        for(auto b:game->self()->getUnits())if(live(b) && b->getType().isRefinery())resource(b);
        return world;
    }
    bool standbyOpen(Position p,const std::vector<Unit>& units,const Standby::World& world,double clearance) const {
        if(!p.isValid() || p.x<clearance || p.y<clearance || p.x+clearance>=game->mapWidth()*32 ||
            p.y+clearance>=game->mapHeight()*32 || !Standby::open(point(p),clearance,world) || !groundPoint(p,units))return false;
        int r=int(clearance);
        for(Position d:{Position(-r,-r),Position(-r,r),Position(r,-r),Position(r,r)})if(!game->isWalkable(WalkPosition(p+d)))return false;
        return true;
    }
    void prepareStandby(BattleTask& task,const std::vector<Unit>& units) {
        std::set<int> alive;for(auto u:units)alive.insert(u->getID());
        for(auto it=task.standbySlots.begin();it!=task.standbySlots.end();) {
            if(!alive.count(it->first)){task.standbyGuards.erase(it->first);it=task.standbySlots.erase(it);}else ++it;
        }
        for(auto u:units)if(task.standbySlots.count(u->getID()))
            task.standbyGuards[u->getID()].ready(frame,point(u->getPosition()),point(task.standbySlots.at(u->getID())));
        if(task.standbyInPlace) {
            // Empty-area key 7 keeps every existing member's exact position.
            // Only later reinforcements need an open post near this anchor.
            auto world=standbyWorld();auto offsets=Standby::offsets(int(units.size()+16));
            for(auto u:units)if(!task.standbySlots.count(u->getID()))for(auto offset:offsets) {
                Position p=task.standbyOrigin+Position(int(offset.x),int(offset.y));
                double clearance=std::max(24.0,std::max(u->getType().width(),u->getType().height())/2.0+8);
                if(!standbyOpen(p,{u},world,clearance) || std::any_of(task.standbySlots.begin(),task.standbySlots.end(),
                    [&](const auto& post){return post.second.getDistance(p)<Standby::spacing-1;}))continue;
                task.standbySlots[u->getID()]=p;task.standbyGuards.try_emplace(u->getID());break;
            }
            return;
        }
        if(frame-task.standbyPlan<24)return;
        task.standbyPlan=frame;
        if(!task.standbyOrigin.isValid()) {
            task.standbyOrigin=groupCenter(units);double nearest=Standby::baseRadius;
            for(auto b:bases)if(live(b) && b->getPosition().getDistance(task.standbyOrigin)<nearest) {
                nearest=b->getPosition().getDistance(task.standbyOrigin);task.standbyBase=b->getPosition();
            }
            task.standbyNearBase=task.standbyBase.isValid();task.standbyFacing=enemyStart;
            if(task.standbyNearBase && task.goal.isValid() && task.goal.getDistance(task.standbyBase)>192)task.standbyFacing=task.goal;
        }
        if(task.standbyNearBase && std::none_of(bases.begin(),bases.end(),[&](Unit b){return live(b) && b->getDistance(task.standbyBase)<192;}))
            task.standbyNearBase=false;
        auto world=standbyWorld();double clearance=24;
        for(auto u:units)clearance=std::max(clearance,std::max(u->getType().width(),u->getType().height())/2.0+8);
        auto suitable=[&](Position p) {
            return Standby::area(point(p),task.standbyNearBase,point(task.standbyBase),point(task.standbyOrigin)) &&
                standbyOpen(p,units,world,clearance);
        };
        bool intact=!task.standbySlots.empty();
        for(auto u:units)if(!task.standbySlots.count(u->getID()) || !suitable(task.standbySlots.at(u->getID())))intact=false;
        if(intact)return;
        auto pp=Standby::preferred(point(task.standbyOrigin),task.standbyNearBase,point(task.standbyBase),point(task.standbyFacing));
        Position preferred(int(pp.x),int(pp.y));auto risks=threats(true);
        std::vector<std::pair<double,Position>> anchors;
        auto consider=[&](Position p,bool existing=false) {
            if(!suitable(p))return;
            double risk=Rules::danger(point(p),risks);if(risk>.2 && !existing)return;
            double cost=risk*10000+p.getDistance(preferred)+p.getDistance(task.standbyOrigin)*.15-(existing?1000000:0);
            anchors.push_back({cost,p});
        };
        if(!task.standbySlots.empty())consider(task.goal,true);
        consider(preferred);consider(task.standbyOrigin);
        for(int r:{96,192,288,384,512})for(int i=0;i<16;++i) {
            double a=i*3.141592653589793/8;consider(preferred+Position(int(std::cos(a)*r),int(std::sin(a)*r)));
        }
        std::sort(anchors.begin(),anchors.end(),[](const auto& a,const auto& b){return a.first<b.first;});
        std::map<int,Position> best;Position bestAnchor=task.standbyOrigin;
        auto offsets=Standby::offsets(int(units.size()));
        for(size_t index=0;index<std::min(size_t(8),anchors.size());++index) {
            Position anchor=anchors[index].second;std::map<int,Position> slots;
            auto free=[&](Position p,bool existing=false) {
                if(!suitable(p) || (!existing && Rules::danger(point(p),risks)>.2))return false;
                for(const auto& s:slots)if(s.second.getDistance(p)<Standby::spacing-1)return false;
                return true;
            };
            // Keep valid assigned posts when production adds reinforcements.
            if(anchor==task.goal)for(auto u:units) {
                auto old=task.standbySlots.find(u->getID());
                if(old!=task.standbySlots.end() && free(old->second,true))slots[u->getID()]=old->second;
            }
            for(auto u:units)if(!slots.count(u->getID()))for(auto offset:offsets) {
                Position p=anchor+Position(int(offset.x),int(offset.y));
                if(free(p)){slots[u->getID()]=p;break;}
            }
            if(slots.size()>best.size()){best=std::move(slots);bestAnchor=anchor;}
            if(best.size()==units.size())break;
        }
        for(auto u:units) {
            auto old=task.standbySlots.find(u->getID()),next=best.find(u->getID());
            if(old==task.standbySlots.end() || next==best.end() || old->second!=next->second) {
                task.standbyGuards.erase(u->getID());task.targeting.erase(u->getID());task.targetFacts.erase(u->getID());
            }
        }
        task.goal=bestAnchor;task.standbySlots=std::move(best);
        for(const auto& slot:task.standbySlots)task.standbyGuards.try_emplace(slot.first);
        log<<"{\"event\":\"clear_standby_plan\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
           <<",\"policy\":\"clear_open_posts_v1\",\"near_base\":"<<(task.standbyNearBase?"true":"false")
           <<",\"origin\":["<<task.standbyOrigin.x<<","<<task.standbyOrigin.y<<"],\"base\":["<<task.standbyBase.x<<","<<task.standbyBase.y
           <<"],\"goal\":["<<task.goal.x<<","<<task.goal.y<<"],\"waiting_space\":"<<(units.size()-task.standbySlots.size())
           <<",\"clearance\":"<<clearance<<",\"slots\":[";
        bool comma=false;for(const auto& slot:task.standbySlots){if(comma)log<<",";comma=true;log<<"["<<slot.first<<","<<slot.second.x<<","<<slot.second.y<<"]";}
        log<<"],\"obstacles\":[";comma=false;
        for(const auto& b:world.buildings){if(comma)log<<",";comma=true;log<<"["<<b.center.x<<","<<b.center.y<<","<<b.halfWidth<<","<<b.halfHeight<<"]";}
        log<<"],\"lanes\":[";comma=false;
        for(const auto& l:world.lanes){if(comma)log<<",";comma=true;log<<"["<<l.from.x<<","<<l.from.y<<","<<l.to.x<<","<<l.to.y<<","<<l.width<<"]";}
        log<<"]}"<<std::endl;
    }
    void applyKeys() {
        for(int raw:keys) {
            bool safeRetreat=raw==CommanderKeys::safeRally;
            int key=safeRetreat?'8':raw;
            if(key<'0' || key>'9')continue;
            feedback="applied";Plans::Result result;std::string action;
            auto previousMode=orders.selected;
            Position origin=center(),goal=Positions::None;
            int releasedProbe=-1;
            if(key=='0') {
                std::vector<Plans::Candidate> candidates;for(auto u:army)candidates.push_back({u->getID(),true,true});
                result=orders.reset(candidates);feedback=result.status;action="restore_auto";
                for(int id:result.manualUnits)releaseUnit(ours(id),"army");
                if(ours(evadeId)){releasedProbe=evadeId;releaseUnit(ours(evadeId),"probe");}
                evadeId=-1;evadeStatus="idle";evadeHome=false;evadeGoal=Positions::None;evadeOrigin=Positions::None;
                for(const auto& escort:escorts)releaseUnit(ours(escort.first),"observer");
                escorts.clear();
                battleTasks.clear();battleLastOrder.clear();battleLastTask.clear();
                miningLocked=false;ProbeCommandGuard::releaseAll(this);miningBuilds.clear();miningResources.clear();miningSettle.clear();
            }
            else if(key=='1') {startEvade();action="scout_evade";}
            else if(key=='2') {startMining(result);action="mine_lock";}
            else if(key=='3') {
                std::vector<Unit> candidates=army;
                // Preserve the forward elements of an existing full-team task;
                // units left behind return to base instead of continuing it.
                Position previousGoal=battleTasks.count(orders.mainTask)?battleTasks.at(orders.mainTask).goal:enemyStart;
                std::stable_sort(candidates.begin(),candidates.end(),[&](Unit a,Unit b){return a->getDistance(previousGoal)<b->getDistance(previousGoal);});
                std::vector<Plans::Candidate> available;
                for(auto u:candidates)available.push_back({u->getID(),health(u)>=.7,u->getType()!=Protoss_Reaver});
                result=orders.split(available);feedback=result.status;action="split_squad";
                if(result.reserveTask) {
                    if(result.inheritedTask) {
                        BattleTask inherited=battleTasks.at(result.inheritedTask);
                        inherited.id=result.task;inherited.scope=Plans::Scope::Squad;
                        battleTasks.emplace(inherited.id,inherited);
                    }
                    BattleTask reserve;reserve.id=result.reserveTask;reserve.key=3;
                    reserve.scope=Plans::Scope::All;reserve.action=Plans::Action::Defend;
                    reserve.goal=defendedBase();reserve.phase="returning";
                    battleTasks.emplace(reserve.id,reserve);goal=reserve.goal;
                }
            }
            else if(key=='4') {
                orders.select(Plans::Scope::All);
                feedback="mode_selected";action="select_mode";
            } else {
                Plans::Action requested=key=='5'?Plans::Action::Expansion:key=='6'?Plans::Action::MainBase:
                    key=='8'?(safeRetreat?Plans::Action::Retreat:Plans::Action::Gather):key=='7'?Plans::Action::Clear:Plans::Action::Defend;
                action=Plans::actionName(requested);
                int depot=key=='5'?expansionTarget():key=='6'?mainBuildingTarget():-1;
                if(depot>=0)goal=remembered.at(depot).position;else if(key=='6')goal=enemyStart;
                else if(key=='9')goal=defendedBase();
                if(key=='5' && goal==Positions::None)feedback="expansion_unknown";
                else {
                    auto candidates=army;
                    if(goal.isValid())std::stable_sort(candidates.begin(),candidates.end(),[&](Unit a,Unit b){return a->getDistance(goal)<b->getDistance(goal);});
                    std::vector<Plans::Candidate> available;
                    for(auto u:candidates)available.push_back({u->getID(),health(u)>=.7,u->getType()!=Protoss_Reaver});
                    int existing=orders.selected==Plans::Scope::Squad?orders.squadTask:orders.mainTask;
                    auto previous=battleTasks.find(existing);
                    bool confirmed=previous!=battleTasks.end() && previous->second.key==key-'0' && previous->second.scope==orders.selected &&
                        ((key>='5' && key<='7' && (key=='7' || (!previous->second.raid.withdrawing &&
                          ((key=='6' && previous->second.mainArea) || previous->second.raidGoal==goal)))) ||
                         (key=='8' && (safeRetreat?previous->second.rendezvous:previous->second.stationary)));
                    if(confirmed) {
                        result.accepted=true;result.task=existing;result.status="task_confirmed";
                        for(auto u:taskUnits(existing))result.units.insert(u->getID());
                        if(key=='7') {
                            auto& task=previous->second;auto members=taskUnits(existing);origin=groupCenter(members);
                            task.encounter=CommanderClear::Encounter{};task.encounter.origin=point(origin);
                            task.standbyOrigin=origin;task.goal=origin;
                            for(auto u:members) {
                                task.standbySlots[u->getID()]=u->getPosition();
                                auto& engagement=task.targeting[u->getID()];
                                if(engagement.target<0){engagement.releasing=false;engagement.chaseSince=-1;}
                            }
                            result.status="patrol_reanchored";
                            log<<"{\"event\":\"patrol_reanchored\",\"frame\":"<<frame<<",\"task_id\":"<<existing
                               <<",\"origin\":["<<origin.x<<","<<origin.y<<"],\"preserved_focus\":true}"<<std::endl;
                        }
                        goal=previous->second.goal;
                    } else result=key=='9'?orders.defendAll(available):orders.assign(available);
                    feedback=result.status;
                    if(result.accepted && !confirmed) {
                        auto units=taskUnits(result.task);origin=groupCenter(units);
                        BattleTask task;task.id=result.task;task.key=key-'0';task.scope=orders.selected;task.action=requested;
                        if(key=='5' || key=='6') {
                            for(auto u:units)task.assaultMembers.insert(u->getID());
                            task.raidGoal=goal;
                            if(depot>=0) {
                                const auto& memory=remembered.at(depot);
                                task.raid.depot=depot;task.raidTile=memory.tile;task.raidType=memory.type;
                            } else {
                                task.raid.expectedSite=true;task.raidTile=TilePosition(enemyStart-Position(64,48));
                                task.raidType=Protoss_Nexus;
                            }
                            if(key=='6')initializeMainArea(task,units);
                            log<<"{\"event\":\"building_target_locked\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
                               <<",\"depot_id\":"<<depot<<",\"key\":"<<(key-'0')<<",\"goal\":["<<goal.x<<","<<goal.y<<"]}"<<std::endl;
                        }
                        if(key=='7') {
                            task.clearTask=true;task.encounter.origin=point(origin);task.phase="patrol_waiting";
                            goal=origin;task.standbyInPlace=true;task.standbyOrigin=origin;
                            for(auto u:units)task.standbySlots[u->getID()]=u->getPosition();
                        }
                        if(key=='8' && !safeRetreat) {
                            task.stationary=true;task.phase="assembly_joining";
                            auto front=previous!=battleTasks.end()?assaultCore(previous->second,units):units;
                            goal=assemblyFront(front);task.goal=goal;
                            for(auto u:units)task.rallyInitial.insert(u->getID());
                            if(!units.empty()) {
                                // Prepare once with the chosen front cluster,
                                // then keep this anchor even as new units join.
                                task.assemblyOrigin=goal;
                                log<<"{\"event\":\"assembly_point\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
                                   <<",\"origin\":["<<goal.x<<","<<goal.y<<"],\"policy\":\"shared_local_refuge_v2\"}"<<std::endl;
                                prepareAssembly(task,units);
                            }
                        }
                        if(key=='8' && safeRetreat) {
                            task.rendezvous=true;task.phase="rally_withdrawing";task.goal=home;
                            for(auto u:units)task.rallyInitial.insert(u->getID());
                            auto hazards=rallyHazards();prepareRally(task,units,hazards);
                            for(auto u:units)if(Rally::frontUnit(point(home),point(task.rallyFront),point(task.goal),point(u->getPosition()),closeThreat(u,448)!=nullptr))
                                task.rallyFrontIds.insert(u->getID());
                            goal=task.goal;
                        }
                        task.goal=goal;battleTasks.emplace(task.id,task);
                    }
                }
            }
            hudLastKey=key-'0';hudLastSafeRally=safeRetreat;hudPhysicalKey=CommanderKeys::physical(raw);hudCommandUnits=int(result.units.size());hudReserves=int(result.reserves.size());hudLastMode=orders.selected;
            lastHudCommand=frame;
            log<<"{\"event\":\"command\",\"schema\":6,\"keymap\":\""<<CommanderKeys::version<<"\",\"frame\":"<<frame<<",\"key\":\""<<hudPhysicalKey
               <<"\",\"modifier\":\"none\",\"action\":\""<<action<<"\",\"mode\":\""<<Plans::scopeName(orders.selected)
               <<"\",\"status\":\""<<feedback<<"\",\"task_id\":"<<result.task<<",\"units\":";writeIds(result.units);
            log<<",\"unit_count\":"<<result.units.size()<<",\"squad_ids\":";writeIds(orders.squad);
            log<<",\"reserve_task\":"<<result.reserveTask<<",\"inherited_task\":"<<result.inheritedTask<<",\"reserve_units\":";writeIds(result.reserves);
            if(key=='0') {
                log<<",\"reset_scope\":\"global\",\"released_probe\":"<<releasedProbe<<",\"released_tasks\":";writeIds(result.releasedTasks);
                log<<",\"released_units\":";writeIds(result.manualUnits);
                log<<",\"previous_mode\":\""<<Plans::scopeName(previousMode)<<"\",\"evade\":\"idle\",\"probe_id\":-1";
            }
            std::set<int> allIds;for(auto u:army)if(live(u))allIds.insert(u->getID());
            log<<",\"army_ids\":";writeIds(allIds);
            log<<",\"mining_lock\":"<<(miningLocked?"true":"false")<<",\"main_task\":"<<orders.mainTask<<",\"squad_task\":"<<orders.squadTask
               <<",\"stance\":\""<<taskLabel(orders.mainTask)<<"\",\"origin\":["<<origin.x<<","<<origin.y
               <<"],\"goal\":["<<goal.x<<","<<goal.y<<"]";
            if(key=='8' && result.accepted)log<<(safeRetreat?",\"rendezvous_policy\":\"shared_safe_point_v1\"":",\"assembly_policy\":\"shared_local_refuge_v2\"");
            if(key=='7')log<<",\"clear_policy\":\"local_guard_v4\"";
            if(key=='9')log<<",\"global_defence\":true,\"multibase_policy\":\"proportional_reinforce_v1\"";
            if(key=='7' || key=='8' || key=='9' || key=='3')log<<",\"army_worker_policy\":\"ignore_worker_bait_v1\"";
            if(key=='1')log<<",\"probe_id\":"<<(feedback=="no_scout_probe"?-1:evadeId)
                <<",\"evade\":\""<<evadeStatus<<"\",\"evade_goal\":["<<evadeGoal.x<<","<<evadeGoal.y<<"]";
            log<<"}"<<std::endl;
        }
        keys.clear();
    }
    std::vector<Targeting::Candidate> targetCandidates(Unit u,const BattleTask& task,const Targeting::Engagement& engagement) const {
        std::vector<Targeting::Candidate> result;
        for(auto e:foes)if(live(e) && e->isVisible() && e->isDetected()) {
            double d=u->getDistance(e);if(d>Targeting::acquireRadius)continue;
            auto type=e->getType();auto weapon=u->isFlying()?type.airWeapon():type.groundWeapon();
            bool operational=e->isCompleted() && (!type.isBuilding() || !type.requiresPsi() || e->isPowered());
            Unit victim=e->getOrderTarget();if(!victim)victim=e->getTarget();
            bool knownVictim=live(victim) && victim->getPlayer()==game->self();
            bool engaged=e->isAttacking() || e->isStartingAttack() || e->isAttackFrame() ||
                e->getOrder()==Orders::AttackUnit || e->getOrder()==Orders::AttackMove;
            int reach=weapon==WeaponTypes::None?0:e->getPlayer()->weaponMaxRange(weapon);
            // Use actual weapons/abilities, not a Dragoon/Cannon-only list.
            // Bunkers and Carriers deliver damage through carried units and do
            // not expose an ordinary ground/air weapon on their parent type.
            bool platform=type==Terran_Bunker || type==Protoss_Carrier;
            bool canHarm=weapon!=WeaponTypes::None || platform || (type==Protoss_Reaver && !u->isFlying());
            bool damagingAbility=false;
            for(auto ability:type.abilities()) {
                auto damage=ability.getWeapon();
                if(damage!=WeaponTypes::None && damage.damageAmount()>0 && (u->isFlying()?damage.targetsAir():damage.targetsGround())) {
                    damagingAbility=true;reach=std::max(reach,damage.maxRange());
                }
            }
            if(platform)reach=std::max(reach,256);
            bool canThreaten=armed(e) || platform || damagingAbility;
            bool firingNearby=canHarm && e->isAttacking() && std::any_of(army.begin(),army.end(),[&](Unit ally){return live(ally) && e->getDistance(ally)<=reach+48;});
            Targeting::Candidate c;c.id=e->getID();c.distance=d;c.health=health(e);
            auto delta=e->getPosition()-u->getPosition();
            c.fleeing=e->getVelocityX()*delta.x+e->getVelocityY()*delta.y>std::max(1.0,e->getPosition().getDistance(u->getPosition()))*.5;
            c.worker=type.isWorker();c.building=type.isBuilding();
            c.stationary=c.building || !type.canMove() || e->isBurrowed() || e->isSieged();
            c.activeThreat=operational && canThreaten && ((knownVictim && engaged) || firingNearby);
            c.armed=operational && canThreaten && (canHarm || damagingAbility);
            c.attackingSelf=c.activeThreat && victim==u;
            c.nearbyThreat=operational && canThreaten && !c.worker && (canHarm || damagingAbility) && d<=std::max(256,reach+96);
            c.importantBuilding=c.building && (type.isResourceDepot() || type.isRefinery() || type.canProduce() ||
                !type.researchesWhat().empty() || !type.upgradesWhat().empty());
            c.objectiveBuilding=c.building && type.isResourceDepot() && e->getDistance(task.goal)<320;
            c.inRange=u->isInWeaponRange(e);c.anchorDistance=engagement.anchored?e->getPosition().getDistance(Position(int(engagement.anchor.x),int(engagement.anchor.y))):0;
            c.valid=u->canAttack(e) && (c.inRange || u->isFlying() || u->hasPath(e->getPosition()));
            if(task.action==Plans::Action::Expansion || task.action==Plans::Action::MainBase) {
                c.objectiveBuilding=task.mainArea?e->getID()==task.mainTarget:e->getID()==task.raid.depot && e->getDistance(task.raidGoal)<192;
                c.valid=c.valid && CommanderRaid::permits(c.objectiveBuilding,c.activeThreat,c.inRange,c.stationary,
                                                         e->getDistance(task.raidGoal),combatEnemy(e) || (c.building && c.armed),c.worker);
                if(task.assaultJoining.count(u->getID()) && c.objectiveBuilding)c.valid=false;
            }
            if(task.clearTask && !cleared(task) &&
               (!CommanderClear::target(c.worker,c.building,combatEnemy(e),c.activeThreat,
                 e->getDistance(patrolPost(task,u)),c.building && canThreaten,
                 CommanderClear::localDefence(c.worker,combatEnemy(e) || (c.building && canThreaten),c.activeThreat,c.inRange,c.fleeing,c.distance)) ||
                !CommanderClear::approach(c.stationary,c.inRange,c.fleeing,c.distance)))c.valid=false;
            if(cleared(task)) {
                auto post=task.standbySlots.find(u->getID());auto guard=task.standbyGuards.find(u->getID());
                if(post==task.standbySlots.end() || guard==task.standbyGuards.end() || (!combatEnemy(e) && !c.activeThreat) ||
                    !guard->second.permits(frame,point(u->getPosition()),point(post->second),point(e->getPosition()),c.inRange))c.valid=false;
            } else if(task.action==Plans::Action::Gather && (u->getDistance(task.goal)>160 || e->getDistance(task.goal)>192))c.valid=false;
            if(task.clearTask && c.worker)c.valid=false; // Also excludes worker self-defence while waiting.
            int workerRadius=task.action==Plans::Action::Expansion && e->getDistance(task.goal)<352?256:192;
            if(c.worker && !c.activeThreat && d>workerRadius)c.valid=false;
            result.push_back(c);
        }
        return result;
    }
    std::vector<Targeting::Candidate> prepareCandidates(Unit u,BattleTask& task) {
        // Key 7/8 have their own run-first controller and never enter targeting.
        auto& engagement=task.targeting[u->getID()];auto position=point(u->getPosition());
        int oldTarget=engagement.target;
        if(oldTarget>=0 && !live(game->getUnit(oldTarget))) {
            engagement.target=-1;engagement.anchored=false;engagement.chaseSince=-1;
        }
        engagement.resume(frame,position,task.clearTask?Rules::distance(position,task.encounter.origin):u->getDistance(task.goal));
        auto candidates=targetCandidates(u,task,engagement);
        if(engagement.anchored && engagement.target>=0 && !engagement.releasing) {
            Targeting::Candidate previous;previous.id=engagement.target;previous.valid=false;
            Unit old=enemy(engagement.target);
            if(old)previous.building=old->getType().isBuilding();
            for(const auto& c:candidates)if(c.id==engagement.target)previous=c;
            if(engagement.needsRelease(frame,position,previous)) {
                log<<"{\"event\":\"target_release\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
                   <<",\"unit_id\":"<<u->getID()<<",\"target_id\":"<<engagement.target
                   <<",\"anchor_distance\":"<<Rules::distance(position,engagement.anchor)<<",\"reason\":\"bounded_pursuit\"}"<<std::endl;
                engagement.release(frame,position);
            }
        }
        return candidates;
    }
    void prepareFocus(BattleTask& task,const std::vector<Unit>& units) {
        task.candidateCache.clear();task.focusChoices.clear();
        if(task.action==Plans::Action::Retreat || task.action==Plans::Action::Defend) {
            task.focus.clear();return;
        }
        std::vector<Targeting::Fighter> fighters;
        for(auto u:units) {
            auto candidates=prepareCandidates(u,task);
            Targeting::Fighter fighter{u->getID(),point(u->getPosition()),{}};
            const auto& engagement=task.targeting.at(u->getID());
            for(const auto& c:candidates)if(engagement.choose(frame,fighter.position,{c}).id==c.id)fighter.candidates.push_back(c);
            if(!fighter.candidates.empty())fighters.push_back(std::move(fighter));
            task.candidateCache.emplace(u->getID(),std::move(candidates));
        }
        task.focusChoices=task.focus.update(std::move(fighters));
    }
    Unit battleEnemy(Unit u,BattleTask& task) {
        if(task.action==Plans::Action::Retreat || task.action==Plans::Action::Defend)return nullptr;
        auto& engagement=task.targeting[u->getID()];auto position=point(u->getPosition());
        const auto& candidates=task.candidateCache.at(u->getID());
        auto focused=task.focusChoices.find(u->getID());
        Targeting::Focus focus=focused==task.focusChoices.end()?Targeting::Focus{}:focused->second;
        auto choice=engagement.choose(frame,position,candidates,focus.target);
        Targeting::Candidate selected;selected.id=-1;
        for(const auto& c:candidates)if(c.id==choice.id)selected=c;
        if(choice.id<0 && engagement.target>=0 && engagement.anchored && !engagement.releasing) {
            double distance=Rules::distance(position,engagement.anchor);
            log<<"{\"event\":\"target_release\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
               <<",\"unit_id\":"<<u->getID()<<",\"target_id\":"<<engagement.target
               <<",\"anchor_distance\":"<<distance<<",\"reason\":\"bounded_pursuit\"}"<<std::endl;
            engagement.release(frame,position);
            choice=engagement.choose(frame,position,candidates,focus.target);
            for(const auto& c:candidates)if(c.id==choice.id)selected=c;
        }
        engagement.advanceOnly=choice.id<0 && (choice.blockedThreat || engagement.blockAutoAcquire(frame,candidates));
        auto previous=task.targetFacts.find(u->getID());
        bool changed=previous==task.targetFacts.end() || previous->second.id!=choice.id ||
            (choice.id>=0 && Targeting::priority(previous->second)!=choice.tier);
        if(changed || frame%120==0) {
            log<<"{\"event\":\"target_selection\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
               <<",\"unit_id\":"<<u->getID()<<",\"action\":\""<<Plans::actionName(task.action)
               <<"\",\"selected\":"<<choice.id<<",\"tier\":"<<choice.tier
               <<",\"focus_target\":"<<focus.target<<",\"focus_group\":"<<focus.group
               <<",\"selection_policy\":\"local_focus_finish_target\",\"decision\":\""<<choice.decision<<"\""
               <<",\"reason\":\""<<(choice.id>=0?Targeting::reason(selected):engagement.releasing?"resume_objective":"no_local_target")
               <<"\",\"releasing\":"<<(engagement.advanceOnly?"true":"false")<<",\"candidates\":[";
            bool comma=false;for(const auto& c:candidates) {
                if(comma)log<<",";comma=true;Unit foe=enemy(c.id);
                bool allowed=engagement.choose(frame,position,{c}).id==c.id;
                log<<"{\"id\":"<<c.id<<",\"type\":"<<(foe?foe->getType().getID():-1)<<",\"tier\":"<<Targeting::priority(c)
                   <<",\"active\":"<<(c.activeThreat?"true":"false")<<",\"worker\":"<<(c.worker?"true":"false")
                   <<",\"building\":"<<(c.building?"true":"false")<<",\"in_range\":"<<(c.inRange?"true":"false")
                   <<",\"important\":"<<(c.importantBuilding?"true":"false")
                   <<",\"objective\":"<<(c.objectiveBuilding?"true":"false")
                   <<",\"stationary\":"<<(c.stationary?"true":"false")
                   <<",\"fleeing\":"<<(c.fleeing?"true":"false")
                   <<",\"armed\":"<<(c.armed?"true":"false")<<",\"attacking_self\":"<<(c.attackingSelf?"true":"false")
                   <<",\"health\":"<<c.health
                   <<",\"distance\":"<<c.distance<<",\"allowed\":"<<(allowed?"true":"false")<<"}";
            }
            log<<"]}"<<std::endl;
        }
        task.targetFacts[u->getID()]=selected;
        if(choice.id<0)return nullptr;
        engagement.commit(frame,position,choice.id,selected.inRange,
                          !selected.building && !selected.stationary);
        return enemy(choice.id);
    }
    std::map<int,int> battleLastTask;
    bool detectionTask(const BattleTask& task) const {
        return CommanderDetection::protects(task.key,task.raid.withdrawing,false,task.stationary);
    }
    bool detectorCovers(Unit detector,Position p) const {
        return live(detector) && detector->isCompleted() && detector->getType().isDetector() &&
            !detector->isBlind() && !detector->isStasised() && !detector->isLockedDown() &&
            !detector->isMaelstrommed() && detector->isPowered() &&
            detector->getPosition().getDistance(p)<= (detector->getType().isBuilding()?224:detector->getPlayer()->sightRange(detector->getType()));
    }
    bool covered(Position p) const {
        for(auto u:game->self()->getUnits())if(detectorCovers(u,p))return true;
        return false;
    }
    // Preserve the Observer heartbeat too: the frozen bot's all-army average
    // must not enqueue a competing destination before our support controller.
    void renewObservers() {
        for(const auto& item:escorts)if(auto u=ours(item.first))
            if(frame-u->getLastCommandFrame()>=8 && item.second.goal.isValid())u->move(item.second.goal);
    }
    std::vector<Unit> assaultCore(const BattleTask& task,const std::vector<Unit>& units) const {
        std::vector<Unit> core;
        for(auto u:units)if(!task.assaultJoining.count(u->getID()))core.push_back(u);
        return core.empty()?units:core;
    }
    void controlObservers() {
        if(frame%8!=0)return;
        std::vector<int> active;
        for(int id:{orders.mainTask,orders.squadTask})if(id && std::find(active.begin(),active.end(),id)==active.end() &&
            battleTasks.count(id) && !taskUnits(id).empty())active.push_back(id);
        // Immediate invisible threats take precedence if only one Observer is
        // available. With two tasks, provide one detector to each before extras.
        std::stable_sort(active.begin(),active.end(),[&](int a,int b){
            const auto& first=battleTasks.at(a);const auto& second=battleTasks.at(b);
            if(first.cloakPauses.size()!=second.cloakPauses.size())return first.cloakPauses.size()>second.cloakPauses.size();
            // Once the contact is resolved, keep scarce detection with the
            // attacking/guarding force rather than returning it to reserves.
            if(detectionTask(first)!=detectionTask(second))return detectionTask(first);
            return taskUnits(a).size()>taskUnits(b).size();
        });
        std::vector<Unit> available;
        for(auto u:game->self()->getUnits())if(live(u) && u->isCompleted() && u->getType()==Protoss_Observer)available.push_back(u);
        auto previous=escorts;escorts.clear();int serial=0;
        while(!active.empty() && !available.empty()) {
            int id=active[serial%active.size()];auto& task=battleTasks.at(id);
            auto units=taskUnits(id);auto core=assaultCore(task,units);Position front=groupCenter(core);
            // Rear reinforcements outside the actual dense fighting group are
            // deliberately excluded, even when the task is full-team.
            Position anchor=front,contact=Positions::None;
            double nearest=1e18;
            for(const auto& pause:task.cloakPauses)if(ours(pause.first)) {
                double d=ours(pause.first)->getDistance(front);
                if(d<nearest){nearest=d;contact=pause.second.threat;anchor=ours(pause.first)->getPosition();}
            }
            if(contact.isValid())anchor=toward(contact,anchor,128);
            else if(detectionTask(task)) {
                Position objective=task.mainArea?task.goal:task.raidGoal.isValid()?task.raidGoal:task.goal;
                Unit leading=nullptr;double best=1e18;
                for(auto u:core)if(u->getDistance(front)<288 && u->getDistance(objective)<best){best=u->getDistance(objective);leading=u;}
                if(leading)anchor=toward(leading->getPosition(),front,std::min(64.0,leading->getPosition().getDistance(front)));
            }
            if(serial>=int(active.size())) {
                Position facing=task.goal.isValid()?task.goal:home;
                double length=std::max(1.0,front.getDistance(facing));
                anchor=clamp(anchor+Position(int(-(facing.y-front.y)*64/length),int((facing.x-front.x)*64/length)));
            }
            auto chosen=std::min_element(available.begin(),available.end(),[&](Unit a,Unit b){
                auto score=[&](Unit u){return u->getDistance(anchor)-(previous.count(u->getID()) && previous.at(u->getID()).task==id?192:0);};
                return score(a)<score(b);
            });
            Unit observer=*chosen;available.erase(chosen);
            Position goal=anchor;double best=1e18;
            // Score safe offsets around the real front. Anti-air avoidance
            // uses air weapon range, never the baseline's distant army mean.
            for(int radius:{0,64,128,192})for(int i=0;i<(radius?16:1);++i) {
                double angle=i*3.141592653589793/8;
                Position p=clamp(anchor+Position(int(std::cos(angle)*radius),int(std::sin(angle)*radius)));
                double risk=0;
                for(auto e:foes)if(e->exists() && e->isVisible() && e->getType().airWeapon()!=WeaponTypes::None) {
                    double reach=e->getPlayer()->weaponMaxRange(e->getType().airWeapon())+96;
                    risk+=std::max(0.0,reach-e->getDistance(p))*100;
                }
                double range=observer->getPlayer()->sightRange(observer->getType())-32;
                double coverage=contact.isValid()?std::max(0.0,p.getDistance(contact)-range)*20:0;
                double score=risk+coverage+p.getDistance(anchor)+observer->getDistance(p)*.02;
                if(score<best){best=score;goal=p;}
            }
            escorts[observer->getID()]={id,goal};
            auto cmd=observer->getLastCommand();
            bool changed=!previous.count(observer->getID()) || previous.at(observer->getID()).task!=id ||
                previous.at(observer->getID()).goal.getDistance(goal)>=24;
            bool accepted=true;
            if(changed || cmd.getType()!=UnitCommandTypes::Move || cmd.getTargetPosition().getDistance(goal)>=24 ||
                frame-observer->getLastCommandFrame()>=8)accepted=observer->move(goal);
            if(changed || frame%120==0)log<<"{\"event\":\"observer_escort\",\"frame\":"<<frame<<",\"task_id\":"<<id
                <<",\"unit_id\":"<<observer->getID()<<",\"front\":["<<front.x<<","<<front.y<<"],\"goal\":["<<goal.x<<","<<goal.y
                <<"],\"position\":["<<observer->getPosition().x<<","<<observer->getPosition().y<<"],\"accepted\":"<<(accepted?"true":"false")
                <<",\"sight\":"<<observer->getPlayer()->sightRange(observer->getType())<<"}"<<std::endl;
            ++serial;
        }
        for(const auto& old:previous)if(!escorts.count(old.first))releaseUnit(ours(old.first),"observer");
    }
    bool cloakSafeAttack(Unit u,Unit target,const CloakPause& pause) const {
        if(!live(target) || !target->isVisible() || !target->isDetected() || target->getType().isWorker() ||
           !u->canAttack(target) || (!combatEnemy(target) && !(target->getType().isBuilding() && armed(target))))return false;
        if(!u->isInWeaponRange(target) && u->getDistance(target)>128)return false;
        auto weapon=target->isFlying()?u->getType().airWeapon():u->getType().groundWeapon();
        double stand=std::max(32.0,double(u->getPlayer()->weaponMaxRange(weapon)+u->getType().width()/2+target->getType().width()/2-8));
        Position to=u->isInWeaponRange(target)?u->getPosition():toward(target->getPosition(),u->getPosition(),stand);
        if(!CommanderDetection::safeCombat(point(u->getPosition()),point(to),point(pause.threat)))return false;
        for(auto e:foes)if(e->exists() && e->isVisible() && !e->isDetected() && !e->getType().isWorker() &&
            e->getType().groundWeapon()!=WeaponTypes::None && !CommanderDetection::safeCombat(point(u->getPosition()),point(to),point(e->getPosition())))return false;
        return true;
    }
    Unit cloakSafeEnemy(Unit u,BattleTask& task,const CloakPause& pause) {
        if(task.stationary) {auto e=assemblyEnemy(u,task);return e && cloakSafeAttack(u,e,pause)?e:nullptr;}
        auto& engagement=task.targeting[u->getID()];auto candidates=targetCandidates(u,task,engagement);
        for(auto& c:candidates)if(c.valid && !cloakSafeAttack(u,enemy(c.id),pause))c.valid=false;
        auto choice=engagement.choose(frame,point(u->getPosition()),candidates);
        if(choice.id<0)return nullptr;
        for(const auto& c:candidates)if(c.id==choice.id)engagement.commit(frame,point(u->getPosition()),c.id,c.inRange,!c.building && !c.stationary);
        return enemy(choice.id);
    }
    bool cloakProtection(Unit u,BattleTask& task) {
        if(!CommanderDetection::protects(task.key,task.raid.withdrawing,u->isFlying(),task.stationary))return false;
        auto found=task.cloakPauses.find(u->getID());Unit hidden=nullptr;double nearest=CommanderDetection::contactRadius;
        for(auto e:foes)if(e->exists() && e->isVisible() && !e->isDetected() && !e->getType().isWorker() &&
            e->getType().groundWeapon()!=WeaponTypes::None && e->getDistance(u)<nearest) {
            nearest=e->getDistance(u);hidden=e;
        }
        if(hidden) {
            auto& pause=task.cloakPauses[u->getID()];
            if(pause.enemy!=hidden->getID())pause.lastPlan=-10000;
            pause.enemy=hidden->getID();pause.lastSeen=frame;pause.threat=hidden->getPosition();
            found=task.cloakPauses.find(u->getID());
        }
        if(found==task.cloakPauses.end())return false;
        auto& pause=found->second;Unit contact=game->getUnit(pause.enemy);
        bool detected=contact && contact->isVisible() && contact->isDetected();
        bool checked=game->isVisible(TilePosition(pause.threat)) && covered(pause.threat) && (!contact || !contact->isVisible());
        if(CommanderDetection::release(!destroyedEnemies.count(pause.enemy),detected,checked,frame-pause.lastSeen,u->getDistance(pause.threat))) {
            log<<"{\"event\":\"detection_resumed\",\"frame\":"<<frame<<",\"task_id\":"<<task.id<<",\"key\":"<<task.key
               <<",\"unit_id\":"<<u->getID()<<",\"enemy_id\":"<<pause.enemy<<",\"detected\":"<<(detected?"true":"false")<<"}"<<std::endl;
            task.cloakPauses.erase(found);return false;
        }
        if(Unit safe=cloakSafeEnemy(u,task,pause)) {
            bool changed=pause.safeTarget!=safe->getID();pause.safeTarget=safe->getID();pause.evading=false;
            battleOrder(u,task,safe->getPosition(),safe,false,false);
            if(changed || frame%24==0)log<<"{\"event\":\"detection_skirmish\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
                <<",\"unit_id\":"<<u->getID()<<",\"enemy_id\":"<<pause.enemy<<",\"target_id\":"<<safe->getID()
                <<",\"position\":["<<u->getPosition().x<<","<<u->getPosition().y<<"],\"threat\":["<<pause.threat.x<<","<<pause.threat.y
                <<"],\"target_position\":["<<safe->getPosition().x<<","<<safe->getPosition().y
                <<"],\"state\":\"detection_skirmishing\",\"task_preserved\":true}"<<std::endl;
            return true;
        }
        pause.safeTarget=-1;
        bool attacking=hidden && hidden->getOrderTarget()==u;
        bool evading=CommanderDetection::evade(u->getDistance(pause.threat),attacking);
        if(!pause.goal.isValid() || frame-pause.lastPlan>=24 || (evading && pause.goal.getDistance(pause.threat)<256)) {
            Position goal=u->getPosition();double best=-1e18;
            auto risks=threats(true);
            for(int radius:{0,64,128,192})for(int i=0;i<(radius?16:1);++i) {
                double angle=i*3.141592653589793/8;
                Position p=clamp(u->getPosition()+Position(int(std::cos(angle)*radius),int(std::sin(angle)*radius)));
                if(!groundPoint(p,{u}))continue;
                double separation=p.getDistance(pause.threat);
                for(auto e:foes)if(e->exists() && e->isVisible() && !e->isDetected() && !e->getType().isWorker() && e->getType().groundWeapon()!=WeaponTypes::None)
                    separation=std::min(separation,double(e->getDistance(p)));
                double score=std::min(288.0,separation)*10-Rules::danger(point(p),risks)*256-p.getDistance(u->getPosition())*.4;
                if(score>best){best=score;goal=p;}
            }
            pause.goal=goal;pause.lastPlan=frame;
        }
        if(task.stationary) {
            Position post=task.assemblyRefuge.isValid()?task.assemblyRefuge:task.assemblyOrigin;
            auto choice=assemblyStep(u,task,post,assemblyHazards(task));
            pause.goal=Position(int(choice.position.x),int(choice.position.y));pause.lastPlan=frame;
        }
        bool changed=pause.evading!=evading;pause.evading=evading;
        // Ordinary movement suppresses native automatic acquisition. Never let
        // an empty Reaver's ammunition fallback replace this safety destination.
        battleOrder(u,task,pause.goal,nullptr,false,false);
        if(changed || frame%24==0)log<<"{\"event\":\"detection_guard\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
            <<",\"key\":"<<task.key<<",\"unit_id\":"<<u->getID()<<",\"enemy_id\":"<<pause.enemy
            <<",\"state\":\""<<(evading?"evading_cloak":"waiting_detection")<<"\",\"position\":["<<u->getPosition().x<<","<<u->getPosition().y
            <<"],\"threat\":["<<pause.threat.x<<","<<pause.threat.y<<"],\"goal\":["<<pause.goal.x<<","<<pause.goal.y
            <<"],\"covered\":"<<(covered(pause.threat)?"true":"false")<<",\"observer_count\":"<<escorts.size()<<"}"<<std::endl;
        return true;
    }
    void renewManualOrders() {
        // Renew manually owned army orders before the frozen bot's command
        // interval, so its automatic objectives cannot enter the native queue.
        for(auto u:army)if(live(u)) {
            int id=orders.taskFor(u->getID());auto found=battleTasks.find(id);
            if(found==battleTasks.end())continue;
            auto& task=found->second;
            auto pause=task.cloakPauses.find(u->getID());
            if(pause!=task.cloakPauses.end() && detectionTask(task)) {
                if(frame-u->getLastCommandFrame()>=8) {
                    Unit target=enemy(pause->second.safeTarget);
                    if(target && (!task.stationary || u->isInWeaponRange(target)) && cloakSafeAttack(u,target,pause->second)) {
                        if(!u->isAttackFrame() && !u->isStartingAttack())u->attack(target);
                    } else if(pause->second.goal.isValid())u->move(pause->second.goal);
                }
                continue;
            }
            if(frame-u->getLastCommandFrame()<8 || u->isAttackFrame() || u->isStartingAttack())continue;
            auto cmd=u->getLastCommand();
            if(task.action==Plans::Action::Expansion || task.action==Plans::Action::MainBase || task.clearTask) {
                bool targetAllowed=false;
                if(cmd.getType()==UnitCommandTypes::Attack_Unit && cmd.getTarget()) {
                    auto candidates=targetCandidates(u,task,task.targeting[u->getID()]);
                    for(const auto& c:candidates)if(c.id==cmd.getTarget()->getID() && c.valid)targetAllowed=true;
                }
                if(!targetAllowed) {
                    Position desired=task.mainArea?task.goal:task.raidGoal.isValid()?task.raidGoal:task.goal;
                    if(task.assaultJoining.count(u->getID()) && task.assaultJoinPoint.isValid())desired=task.assaultJoinPoint;
                    if(task.clearTask) {
                        Position post=patrolPost(task,u);
                        bool localSearch=task.encounter.emptySince>=0 && task.encounter.searching(frame) && task.goal.getDistance(post)<=CommanderClear::radius;
                        desired=localSearch?task.goal:post;
                    }
                    Position goal=reachablePoint(desired,{u});
                    cmd=u->getDistance(goal)<64?UnitCommand::holdPosition(u):UnitCommand::move(u,goal);
                }
            } else if((task.action==Plans::Action::Defend || task.stationary) && cmd.getType()==UnitCommandTypes::Attack_Unit &&
                      live(cmd.getTarget()) && !cmd.getTarget()->getType().isWorker() && u->isInWeaponRange(cmd.getTarget())) {
                // Keep the explicit in-range combat target, rather than
                // replacing a legitimate shot with a move on each heartbeat.
            } else if(cmd.getType()!=UnitCommandTypes::Move && cmd.getType()!=UnitCommandTypes::Hold_Position) {
                auto flight=task.flight.find(u->getID());
                cmd=UnitCommand::move(u,flight!=task.flight.end() && flight->second.moveGoal.isValid()?flight->second.moveGoal:task.goal);
            }
            bool accepted=u->issueCommand(cmd);
            if(accepted){battleLastOrder[u->getID()]=frame;battleLastTask[u->getID()]=id;}
            log<<"{\"event\":\"manual_ownership\",\"frame\":"<<frame<<",\"task_id\":"<<id
               <<",\"unit_id\":"<<u->getID()<<",\"accepted\":"<<(accepted?"true":"false")
               <<",\"command_type\":"<<cmd.getType().getID();writeRaid(task);log<<"}"<<std::endl;
        }
    }
    int flightCooldown(Unit u,Unit target) const {
        return target && target->isFlying()?u->getAirWeaponCooldown():u->getGroundWeaponCooldown();
    }
    Unit flightEnemy(Unit u,bool atBase,Position base) const {
        Unit best=nullptr;double nearest=1e18;
        for(auto e:foes)if(live(e) && e->isVisible() && e->isDetected() && armed(e) &&
            !e->getType().isWorker() &&
            (atBase || !e->getType().isBuilding()) && u->canAttack(e) && u->isInWeaponRange(e)) {
            double d=u->getDistance(e);
            if(!atBase && d>Flight::closeDistance)continue;
            if(atBase && e->getDistance(base)>384)continue;
            if(d<nearest){nearest=d;best=e;}
        }
        return best;
    }
    std::vector<Unit> protectedAssets(const BattleTask& task) const {
        std::vector<Unit> result;
        for(auto u:game->self()->getUnits())if(live(u) && (u->getType().isBuilding() || u->getType().isWorker())) {
            double limit=u->getType().isWorker()?CommanderDefence::workerRadius:CommanderDefence::assetRadius;
            if(u->getDistance(task.goal)>limit)continue;
            // A worker/building belongs to its nearest surviving base. Never
            // let assets at another expansion stretch this task's territory.
            bool other=std::any_of(defenceBases.begin(),defenceBases.end(),[&](Unit b){return live(b) && b->getDistance(task.goal)>192 && b->getDistance(u)+64<u->getDistance(task.goal);});
            if(!other)result.push_back(u);
        }
        return result;
    }
    bool attacksAsset(Unit enemyUnit,const std::vector<Unit>& assets,int* id=nullptr) const {
        Unit victim=enemyUnit->getOrderTarget();if(!victim)victim=enemyUnit->getTarget();
        if(!victim || !live(victim))return false;
        bool attacking=enemyUnit->isAttacking() || enemyUnit->isStartingAttack() || enemyUnit->getOrder()==Orders::AttackUnit;
        bool protectedVictim=attacking && std::any_of(assets.begin(),assets.end(),[&](Unit a){return a==victim;});
        if(protectedVictim && id)*id=victim->getID();
        return protectedVictim;
    }
    double assetDistance(Unit u,const std::vector<Unit>& assets,int* closest=nullptr) const {
        double best=1e18;
        for(auto a:assets)if(live(a)) {
            double d=u->getDistance(a);
            if(d<best){best=d;if(closest)*closest=a->getID();}
        }
        return best;
    }
    Position defencePoint(Position desired,const std::vector<Unit>& units,const std::vector<Unit>& assets) const {
        auto open=[&](Position p) {
            if(!groundPoint(p,units))return false;
            for(auto a:assets)if(live(a) && a->getType().isBuilding()) {
                auto delta=p-a->getPosition();
                if(std::abs(delta.x)<a->getType().width()/2+24 && std::abs(delta.y)<a->getType().height()/2+24)return false;
            }
            return true;
        };
        desired=clamp(desired);if(open(desired))return desired;
        for(int r=32;r<=256;r+=32)for(int i=0;i<16;++i) {
            double a=i*3.141592653589793/8;
            Position p=clamp(desired+Position(int(std::cos(a)*r),int(std::sin(a)*r)));
            if(open(p))return p;
        }
        return reachablePoint(desired,units);
    }
    void planDefence(BattleTask& task,const std::vector<Unit>& units,const std::vector<Unit>& assets) {
        if(task.guardPost.isValid() && frame-task.lastGuardPlan<24)return;
        task.lastGuardPlan=frame;
        Position facing=enemyStart;
        const auto& path=BWEM::Map::Instance().GetPath(task.goal,enemyStart);
        if(!path.empty())facing=Position(path.front()->Center());
        Position preferred=toward(task.goal,facing,std::clamp(task.goal.getDistance(facing)+48,224.0,384.0));
        // Cover forward buildings along the entrance, instead of standing
        // behind the Nexus. The post is still capped relative to the base.
        double ax=facing.x-task.goal.x,ay=facing.y-task.goal.y,norm=std::max(1.0,std::hypot(ax,ay));
        double furthest=task.goal.getDistance(preferred);
        for(auto a:assets)if(a->getType().isBuilding()) {
            double projection=((a->getPosition().x-task.goal.x)*ax+(a->getPosition().y-task.goal.y)*ay)/norm;
            if(projection>furthest && a->getDistance(task.goal)<CommanderDefence::postRadius)furthest=projection+80;
        }
        preferred=toward(task.goal,facing,std::min(CommanderDefence::postRadius,furthest));
        Unit danger=nullptr;int victim=-1;double score=1e18;
        for(auto e:foes)if(live(e) && !e->getType().isWorker() && e->isVisible() && e->isDetected() && e->getDistance(task.goal)<=CommanderDefence::zoneRadius) {
            int attacked=-1;bool attacking=attacksAsset(e,assets,&attacked);
            double d=assetDistance(e,assets,attacking?nullptr:&attacked);
            if(!attacking && (!armed(e) || e->getType().isWorker() || d>256))continue;
            double cost=(attacking?0:10000)+d;
            if(cost<score){score=cost;danger=e;victim=attacked;}
        }
        if(danger) {
            Unit asset=ours(victim);Position shelter=asset?asset->getPosition():task.goal;
            preferred=toward(shelter,danger->getPosition(),std::min(96.0,shelter.getDistance(danger->getPosition())));
            if(task.goal.getDistance(preferred)>CommanderDefence::postRadius)preferred=toward(task.goal,preferred,CommanderDefence::postRadius);
            // Low-health defenders use nearby static defenses as cover.
            double oursPower=0,theirsPower=0;for(auto u:units)oursPower+=strength(u);
            for(auto e:foes)if(live(e) && realThreat(e) && e->getDistance(preferred)<384)theirsPower+=strength(e);
            if(theirsPower>oursPower*1.25)for(auto a:assets)if(a->getType().isBuilding() && armed(a) && a->getDistance(preferred)<320) {
                preferred=toward(a->getPosition(),task.goal,64);break;
            }
        }
        preferred=defencePoint(preferred,units,assets);
        // Path fallback must not turn an unreachable post into an enemy-side
        // destination. Keep the last valid base-local post instead.
        if(task.goal.getDistance(preferred)>CommanderDefence::postRadius+64) {
            if(task.guardPost.isValid())return;
            preferred=reachablePoint(task.goal,{});
        }
        if(!task.guardPost.isValid() || victim!=task.guardVictim || task.guardPost.getDistance(preferred)>128) {
            task.guardPost=preferred;task.guardVictim=victim;
            log<<"{\"event\":\"defence_post\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
               <<",\"base\":["<<task.goal.x<<","<<task.goal.y<<"],\"post\":["<<preferred.x<<","<<preferred.y
               <<"],\"protected_asset\":"<<victim<<",\"reason\":\""<<(danger?"protect_threatened_assets":"cover_entrance")<<"\"}"<<std::endl;
        }
    }
    Unit defenceEnemy(Unit u,const BattleTask& task,const std::vector<Unit>& assets,int& victim) const {
        Unit best=nullptr;double score=1e18;
        for(auto e:foes)if(live(e) && !e->getType().isWorker() && u->canAttack(e)) {
            int attacked=-1;bool attacking=attacksAsset(e,assets,&attacked);
            double d=assetDistance(e,assets,attacking?nullptr:&attacked);
            int reach=std::max(e->getType().groundWeapon().maxRange(),e->getType().airWeapon().maxRange());
            bool threat=attacking || (!e->getType().isWorker() && (armed(e) || combatEnemy(e)));
            if(!CommanderDefence::candidate(e->isVisible(),e->isDetected(),threat,attacking,e->getDistance(task.goal),d,reach))continue;
            if(!u->isFlying() && !u->hasPath(e->getPosition()))continue;
            double cost=(attacking?0:10000)+u->getDistance(e)+d*.25;
            if(cost<score){score=cost;best=e;victim=attacked;}
        }
        return best;
    }
    bool briefFlightFire(Unit u,BattleTask& task) {
        auto& f=task.flight[u->getID()];auto& window=f.fire;auto p=point(u->getPosition());
        if(window.since>=0) {
            Unit target=enemy(window.target);
            int closeEnemies=0;
            for(auto e:foes)if(live(e) && realThreat(e) && !e->getType().isBuilding() && u->getDistance(e)<128)++closeEnemies;
            bool close=live(target) && target->isVisible() && target->isDetected() &&
                u->getDistance(target)<=Flight::closeDistance && u->isInWeaponRange(target) && health(u)>=.55 && closeEnemies<=2;
            bool fired=flightCooldown(u,target)>0;
            if(window.keepFiring(frame,p,close,fired))return true;
            log<<"{\"event\":\"flight_burst_end\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
               <<",\"unit_id\":"<<u->getID()<<",\"duration\":"<<frame-window.since
               <<",\"fired\":"<<(fired?"true":"false")<<"}"<<std::endl;
            window.end(frame,p);
        }
        if(!window.canStart(frame,p))return false;
        Unit target=flightEnemy(u,false,task.goal);if(!target)return false;
        int nearby=0;
        for(auto e:foes)if(live(e) && realThreat(e) && !e->getType().isBuilding() && u->getDistance(e)<128)++nearby;
        if(!Flight::opportunity(true,u->isInWeaponRange(target),flightCooldown(u,target),health(u),u->getDistance(target),nearby))return false;
        window.begin(frame,p,target->getID());
        log<<"{\"event\":\"flight_burst_start\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
           <<",\"unit_id\":"<<u->getID()<<",\"enemy_id\":"<<target->getID()<<",\"distance\":"<<u->getDistance(target)
           <<",\"position\":["<<u->getPosition().x<<","<<u->getPosition().y<<"]"
           <<",\"in_range\":true,\"cooldown\":"<<flightCooldown(u,target)<<",\"health\":"<<health(u)
           <<",\"nearby_enemies\":"<<nearby<<",\"run_frames\":"<<frame-window.lastEnd<<"}"<<std::endl;
        return true;
    }
    void writeFlight(Unit u,const BattleTask& task) {
        auto it=task.flight.find(u->getID());if(it==task.flight.end())return;
        const auto& f=it->second;Unit pursuer=enemy(f.nearby);
        const char* stage=task.rendezvous?f.rallyStage.c_str():f.atBase?(f.guard.returning?"base_rejoin":f.intercepting?"base_intercept":"base_guard"):f.fire.since>=0?"brief_fire":f.resting?"safe_hold":"running";
        log<<",\"flight_policy\":\""<<(task.rendezvous?"rendezvous_run_first":task.action==Plans::Action::Defend?"return_then_defend":"run_first")<<"\",\"flight_stage\":\""<<stage<<"\",\"base_distance\":"<<u->getDistance(task.goal)
           <<",\"move_goal\":["<<f.moveGoal.x<<","<<f.moveGoal.y<<"],\"fire_since\":"<<f.fire.since
           <<",\"last_fire_end\":"<<f.fire.lastEnd<<",\"pursuer_id\":"<<f.nearby
           <<",\"pursuer_distance\":"<<(pursuer?u->getDistance(pursuer):-1);
        if(task.action==Plans::Action::Defend)writeDefence(task,u);
        if(task.rendezvous)writeRendezvous(task,u);
    }
    void writeRendezvous(const BattleTask& task,Unit u=nullptr) {
        log<<",\"rendezvous_policy\":\"shared_safe_point_v1\",\"rally_point\":["<<task.goal.x<<","<<task.goal.y
           <<"],\"front_origin\":["<<task.rallyFront.x<<","<<task.rallyFront.y<<"]";
        if(u) {
            const auto& f=task.flight.at(u->getID());
            log<<",\"rally_role\":\""<<f.rallyRole<<"\",\"rally_stage\":\""<<f.rallyStage<<"\",\"rally_risk\":"<<f.rallyRisk
               <<",\"rally_route_peak\":"<<f.rallyPeak<<",\"rally_distance\":"<<u->getPosition().getDistance(task.goal)
               <<",\"rally_path_advance\":"<<f.rallyPathAdvance
               <<",\"rally_step_progress\":"<<Rally::progress(point(u->getPosition()),point(task.goal),point(f.moveGoal));
        } else {
            int waiting=0,joining=0,evading=0,blocked=0,newUnits=0;
            for(auto member:taskUnits(task.id)) {
                auto found=task.flight.find(member->getID());if(found==task.flight.end())continue;
                const auto& f=found->second;
                waiting+=f.rallyStage=="waiting";joining+=f.rallyStage=="joining";
                evading+=f.rallyStage=="evading";blocked+=f.rallyStage=="route_wait";
                newUnits+=f.rallyRole=="new_unit";
            }
            log<<",\"rally_waiting\":"<<waiting<<",\"rally_joining\":"<<joining<<",\"rally_evading\":"<<evading
               <<",\"rally_blocked\":"<<blocked<<",\"rally_new_units\":"<<newUnits;
        }
    }
    void writeDefence(const BattleTask& task,Unit u=nullptr) {
        log<<",\"defence_policy\":\"perimeter_bounded_v1\",\"defence_base\":["<<task.goal.x<<","<<task.goal.y
           <<"],\"guard_post\":["<<task.guardPost.x<<","<<task.guardPost.y<<"]";
        log<<",\"multibase_policy\":\"proportional_reinforce_v1\",\"assigned_base_id\":"<<task.regionalBase;
        if(!u)return;
        const auto& f=task.flight.at(u->getID());Unit target=enemy(f.guard.target);
        Unit victim=target?target->getOrderTarget():nullptr;
        if(!victim && target)victim=target->getTarget();
        log<<",\"defence_target\":"<<f.guard.target<<",\"protected_asset\":"<<f.defendedAsset
           <<",\"defence_active\":"<<(f.atBase?"true":"false")<<",\"defence_rejoining\":"<<(f.guard.returning?"true":"false")
           <<",\"intercepting\":"<<(f.intercepting?"true":"false")<<",\"guard_anchored\":"<<(f.guard.anchored?"true":"false")
           <<",\"guard_origin\":["<<f.guard.origin.x<<","<<f.guard.origin.y<<"],\"guard_since\":"<<f.guard.since
           <<",\"guard_origin_distance\":"<<Rules::distance(point(u->getPosition()),f.guard.origin)
           <<",\"guard_post_distance\":"<<u->getPosition().getDistance(task.guardPost)
           <<",\"defence_target_distance\":"<<(target?target->getPosition().getDistance(task.goal):-1)
           <<",\"defence_target_origin_distance\":"<<(target?Rules::distance(point(target->getPosition()),f.guard.origin):-1)
           <<",\"defence_target_in_range\":"<<(target && u->isInWeaponRange(target)?"true":"false")
           <<",\"defence_attacking_asset\":"<<(live(victim) && victim->getID()==f.defendedAsset && victim->getPlayer()==game->self()?"true":"false")
           <<",\"defence_unit_distance\":"<<u->getPosition().getDistance(task.goal);
    }
    void writeStandby(Unit u,const BattleTask& task,Unit target) {
        auto post=task.standbySlots.find(u->getID());auto guard=task.standbyGuards.find(u->getID());
        log<<",\"standby_policy\":\"clear_open_posts_v1\",\"standby_near_base\":"<<(task.standbyNearBase?"true":"false")
           <<",\"standby_has_post\":"<<(post!=task.standbySlots.end()?"true":"false");
        if(post==task.standbySlots.end())return;
        log<<",\"standby_post\":["<<post->second.x<<","<<post->second.y<<"],\"standby_unit_distance\":"<<u->getPosition().getDistance(post->second)
           <<",\"standby_target_distance\":"<<(target?target->getPosition().getDistance(post->second):-1)
           <<",\"standby_target_in_range\":"<<(target && u->isInWeaponRange(target)?"true":"false")
           <<",\"standby_returning\":"<<(guard!=task.standbyGuards.end() && guard->second.returning?"true":"false");
    }
    void battleOrder(Unit u,BattleTask& task,Position destination,Unit target,bool attackMove,bool holdFire=false) {
        auto nativeTarget=u->getOrderTarget();if(!nativeTarget)nativeTarget=u->getTarget();
        bool ignoreWorkers=task.stationary || task.clearTask || task.action==Plans::Action::Defend;
        bool ignoredWorker=ignoreWorkers && live(nativeTarget) && nativeTarget->getType().isWorker() && game->self()->isEnemy(nativeTarget->getPlayer());
        bool workerNear=ignoreWorkers && !target && std::any_of(foes.begin(),foes.end(),[&](Unit e){
            return live(e) && e->getType().isWorker() && e->getDistance(u)<=std::max(320,u->getType().groundWeapon().maxRange()+96);
        });
        // Native Hold Position can auto-fire at workers despite our target
        // filter. Renew a zero-distance ordinary move while waiting beside
        // workers, so no native idle/hold acquisition can start a chase/shot.
        bool quietWait=(task.stationary || workerNear) && holdFire;
        if(quietWait){holdFire=false;destination=u->getPosition();}
        // An empty Reaver cannot accept BWAPI attack commands. Keep it on the
        // new assault task and move toward that objective while the unchanged
        // baseline produces ammunition; switch to attack as soon as it is ready.
        bool awaitingAmmo=u->getType()==Protoss_Reaver && u->getScarabCount()==0 &&
            (task.action==Plans::Action::MainBase || task.action==Plans::Action::Expansion || task.action==Plans::Action::Clear || cleared(task));
        if(awaitingAmmo && !task.cloakPauses.count(u->getID())){target=nullptr;attackMove=false;if(!task.clearTask)destination=task.goal;}
        auto before=u->getLastCommand();
        auto expected=holdFire?UnitCommandTypes::Hold_Position:target?UnitCommandTypes::Attack_Unit:attackMove?UnitCommandTypes::Attack_Move:UnitCommandTypes::Move;
        auto matches=[&](const UnitCommand& command) {
            return command.getType()==expected && (holdFire || (target?command.getTarget()==target:command.getTargetPosition().getDistance(destination)<24));
        };
        bool same=matches(before);
        bool flightTask=task.action==Plans::Action::Retreat || task.action==Plans::Action::Defend;
        bool boundedAdvance=!target && !attackMove && (task.stationary || task.action==Plans::Action::Expansion || task.action==Plans::Action::MainBase || task.clearTask || (task.targeting.count(u->getID()) && task.targeting.at(u->getID()).advanceOnly));
        // A completed Move can auto-acquire enemies inside StarCraft without
        // changing BWAPI's cached last command. Cancel that native pursuit too.
        bool nativeOrderBlock=(flightTask || boundedAdvance) && !target && !holdFire && u->getOrder()!=Orders::Move && !u->isIdle();
        if(task.action==Plans::Action::Defend && task.flight.at(u->getID()).atBase &&
            !u->isAttacking() && u->getOrder()!=Orders::AttackUnit && u->getOrder()!=Orders::AttackMove)nativeOrderBlock=false;
        if(task.rendezvous && task.flight.at(u->getID()).rallyRisk<=Rally::epsilon &&
            !u->isAttacking() && u->getOrder()!=Orders::AttackUnit && u->getOrder()!=Orders::AttackMove)nativeOrderBlock=false;
        bool nativeChase=nativeOrderBlock && (u->isAttacking() || u->getOrder()==Orders::AttackUnit || u->getOrder()==Orders::AttackMove);
        bool retry=!holdFire && (u->isIdle() || u->isStuck()) && (!target && u->getDistance(destination)>80) && frame-battleLastOrder[u->getID()]>=24;
        if(same && !retry && !nativeOrderBlock && !ignoredWorker && !quietWait && battleLastTask[u->getID()]==task.id)return;
        bool conflict=battleLastTask[u->getID()]==task.id && u->getLastCommandFrame()>battleLastOrder[u->getID()];
        // Safety and task switches intentionally bypass attack-animation and
        // generic move throttles. This executes AFTER the frozen delegate on
        // every frame, so an old baseline attack cannot undo retreat/defense.
        bool accepted=holdFire?u->holdPosition():target?u->attack(target):attackMove?u->attack(destination):u->move(destination);
        int errorCode=accepted?0:game->getLastError().getID();
        auto after=u->getLastCommand();bool verified=matches(after);
        if(accepted){battleLastOrder[u->getID()]=frame;battleLastTask[u->getID()]=task.id;}
        if(conflict)++battleOverrides;
        if(task.stationary && quietWait && same && accepted && verified && !conflict && frame%120!=0)return;
        auto previous=before.getTargetPosition(),final=after.getTargetPosition(),p=u->getPosition();
        log<<"{\"event\":\"battle_order\",\"schema\":4,\"frame\":"<<frame<<",\"task_id\":"<<task.id
           <<",\"unit_id\":"<<u->getID()<<",\"unit_type\":"<<u->getType().getID()<<",\"error\":"<<errorCode
           <<",\"scope\":\""<<Plans::scopeName(task.scope)<<"\",\"action\":\""<<Plans::actionName(task.action)
           <<"\",\"intent\":\""<<(holdFire?"hold_fire":target?"attack_unit":attackMove?"attack_move":"move")<<"\",\"position\":["<<p.x<<","<<p.y
           <<"],\"goal\":["<<destination.x<<","<<destination.y<<"],\"previous_target\":["<<previous.x<<","<<previous.y
           <<"],\"last_target\":["<<final.x<<","<<final.y<<"],\"target_id\":"<<(target?target->getID():-1)
           <<",\"accepted\":"<<(accepted?"true":"false")<<",\"verified\":"<<(verified?"true":"false")
           <<",\"awaiting_ammo\":"<<(awaitingAmmo?"true":"false")
           <<",\"native_pursuit_cancelled\":"<<(nativeChase?"true":"false")
           <<",\"native_order_overridden\":"<<(nativeOrderBlock?"true":"false")
           <<",\"ignored_worker_cancelled\":"<<(ignoredWorker?"true":"false")<<",\"worker_quiet_wait\":"<<(quietWait && workerNear?"true":"false")
           <<",\"stationary_quiet_wait\":"<<(task.stationary && quietWait?"true":"false")
           <<",\"conflict\":"<<(conflict?"true":"false")<<",\"overrides\":"<<battleOverrides;
        if(task.action==Plans::Action::Retreat || task.action==Plans::Action::Defend)writeFlight(u,task);
        else {
            auto facts=task.targetFacts.find(u->getID());
            log<<",\"targeting_policy\":\""<<(task.stationary?"stationary_return_fire_v1":"local_focus_finish_target")<<"\",\"disengaging\":"<<(boundedAdvance?"true":"false");
            if(facts!=task.targetFacts.end() && facts->second.id>=0)
                log<<",\"target_reason\":\""<<Targeting::reason(facts->second)<<"\",\"target_tier\":"<<Targeting::priority(facts->second);
        }
        if(cleared(task))writeStandby(u,task,target);
        if(task.stationary)writeAssembly(task,u);
        writeRaid(task);log<<"}"<<std::endl;
    }
    void taskState(const BattleTask& task,const std::vector<Unit>& units,int arrived) {
        log<<"{\"event\":\"battle_state\",\"schema\":4,\"frame\":"<<frame<<",\"task_id\":"<<task.id
           <<",\"scope\":\""<<Plans::scopeName(task.scope)<<"\",\"action\":\""<<Plans::actionName(task.action)
           <<"\",\"phase\":\""<<task.phase<<"\",\"goal\":["<<task.goal.x<<","<<task.goal.y<<"],\"arrived\":"<<arrived<<",\"units\":[";
        bool comma=false;for(auto u:units){if(comma)log<<",";log<<u->getID();comma=true;}
        log<<"],\"overrides\":"<<battleOverrides;
        if(task.action==Plans::Action::Retreat)log<<",\"flight_policy\":\""<<(task.rendezvous?"rendezvous_run_first":"run_first")<<"\"";
        if(task.action==Plans::Action::Defend) {log<<",\"flight_policy\":\"return_then_defend\"";writeDefence(task);}
        if(task.stationary || task.clearTask || task.action==Plans::Action::Defend || task.action==Plans::Action::Retreat)log<<",\"army_worker_policy\":\"ignore_worker_bait_v1\"";
        if(task.action==Plans::Action::Defend && task.regionalBase<0)writeMultiBase(task,units);
        writeRaid(task);
        if(task.clearTask)log<<",\"clear_task\":true,\"clear_policy\":\"local_guard_v4\",\"standby_in_place\":"<<(task.standbyInPlace?"true":"false")
            <<",\"clear_complete\":false,\"patrol_origin\":["<<task.encounter.origin.x<<","<<task.encounter.origin.y
            <<"],\"last_enemy_seen\":"<<task.encounter.lastSeen<<",\"patrol_radius\":"<<CommanderClear::radius;
        if(cleared(task)) {
            size_t posts=std::count_if(units.begin(),units.end(),[&](Unit u){return task.standbySlots.count(u->getID());});
            log<<",\"standby_policy\":\"clear_open_posts_v1\",\"standby_near_base\":"<<(task.standbyNearBase?"true":"false")
               <<",\"standby_posts\":"<<posts<<",\"standby_waiting_space\":"<<(units.size()-posts);
        }
        if(task.rendezvous)writeRendezvous(task);
        if(task.stationary)writeAssembly(task);
        log<<"}"<<std::endl;
    }
    void writeRaid(const BattleTask& task) {
        if(!task.raidGoal.isValid())return;
        log<<",\"raid_depot\":"<<task.raid.depot<<",\"raid_goal\":["<<task.raidGoal.x<<","<<task.raidGoal.y
           <<"],\"objective_role\":\""<<(task.key==6?"main":task.raidType.isResourceDepot()?"expansion":"near_enemy")<<"\",\"raid_policy\":\""<<(task.mainArea?"main_area_outer_first_v1":"combat_first_objective_v2")<<"\",\"auto_withdrawal\":"<<(task.raid.withdrawing?"true":"false")
           <<",\"reinforcements_joining\":"<<task.assaultJoining.size();
        if(task.mainArea)log<<",\"main_target\":"<<task.mainTarget<<",\"main_known_buildings\":"<<task.mainKnown.size()
            <<",\"main_unchecked\":"<<task.mainUnchecked<<",\"main_region_radius\":768,\"main_stage\":\""<<task.mainStage<<"\"";
    }
    bool inMainArea(const BattleTask& task,Position p) const {
        return CommanderMainArea::contains(point(p),point(task.raidGoal),task.mainOtherBases);
    }
    void initializeMainArea(BattleTask& task,const std::vector<Unit>& units) {
        task.mainArea=true;
        for(const auto& area:BWEM::Map::Instance().Areas())for(const auto& base:area.Bases()) {
            Position p=Position(base.Location())+Position(64,48);
            if(p.getDistance(task.raidGoal)>=448)task.mainOtherBases.push_back(point(p));
        }
        for(int x=std::max(0,task.raidGoal.x/32-24);x<=std::min(game->mapWidth()-1,task.raidGoal.x/32+24);++x)
            for(int y=std::max(0,task.raidGoal.y/32-24);y<=std::min(game->mapHeight()-1,task.raidGoal.y/32+24);++y) {
                Position p(x*32+16,y*32+16);
                if(inMainArea(task,p) && groundPoint(p,units))task.mainSurvey.push_back(p);
            }
        task.mainChecked.resize(task.mainSurvey.size(),false);task.mainUnchecked=int(task.mainSurvey.size());
    }
    bool memoryFootprintVisible(const Memory& m) const {
        if(!m.tile.isValid())return false;
        for(int x=0;x<m.type.tileWidth();++x)for(int y=0;y<m.type.tileHeight();++y)
            if(!game->isVisible(m.tile+TilePosition(x,y)))return false;
        return true;
    }
    int pylonValue(Unit pylon,const std::vector<Unit>& buildings) const {
        if(!pylon || !pylon->isVisible() || pylon->getType()!=Protoss_Pylon || !pylon->isCompleted())return 0;
        int value=0;
        for(auto b:buildings)if(b->getPlayer()==pylon->getPlayer() && b->getType().requiresPsi() && b->isPowered() &&
            CommanderMainArea::powers(point(pylon->getPosition()),point(b->getPosition()))) {
            bool alternative=std::any_of(buildings.begin(),buildings.end(),[&](Unit other){return other!=pylon &&
                other->getPlayer()==pylon->getPlayer() && other->getType()==Protoss_Pylon && other->isCompleted() &&
                CommanderMainArea::powers(point(other->getPosition()),point(b->getPosition()));});
            if(!alternative)value+=armed(b)?2:1;
        }
        return value;
    }
    void beginRaidWithdrawal(BattleTask& task,const std::vector<Unit>& units,const char* reason) {
        task.raid.withdrawing=true;
        task.action=Plans::Action::Retreat;task.phase="rally_withdrawing";task.clearSince=-1;
        task.targeting.clear();task.targetFacts.clear();task.focus.clear();task.flight.clear();task.escapes.clear();
        task.cloakPauses.clear();
        task.rendezvous=true;task.rallyFront=Positions::None;task.rallyInitial.clear();task.rallyFrontIds.clear();
        task.goal=home;
        for(auto u:units)task.rallyInitial.insert(u->getID());
        auto hazards=rallyHazards();prepareRally(task,units,hazards);
        for(auto u:units)if(Rally::frontUnit(point(home),point(task.rallyFront),point(task.goal),point(u->getPosition()),closeThreat(u,448)!=nullptr))
            task.rallyFrontIds.insert(u->getID());
        log<<"{\"event\":\"building_complete\",\"frame\":"<<frame<<",\"task_id\":"<<task.id<<",\"reason\":\""<<reason<<"\"";
        writeRaid(task);log<<"}"<<std::endl;taskState(task,units,0);
    }
    void planMainArea(BattleTask& task,const std::vector<Unit>& units) {
        if(frame-task.mainPlanFrame<8)return;
        task.mainPlanFrame=frame;
        std::vector<Unit> visibleBuildings;Unit localArmy=nullptr;double nearestArmy=1e18;
        std::vector<Unit> core;
        for(auto u:units)if(task.assaultMembers.count(u->getID()) && !task.assaultJoining.count(u->getID()))core.push_back(u);
        Position front=groupCenter(core.empty()?units:core);
        for(auto player:game->enemies())for(auto e:player->getUnits())if(e->exists() && e->isVisible()) {
            if(e->getType().isBuilding()) {
                visibleBuildings.push_back(e);
                if(inMainArea(task,e->getPosition()))task.mainKnown[e->getID()]={e->getPosition(),e->getType(),e->getTilePosition()};
            } else if(combatEnemy(e) && inMainArea(task,e->getPosition()) && e->getDistance(front)<nearestArmy) {
                localArmy=e;nearestArmy=e->getDistance(front);
            }
        }
        // Every retained contact was observed by our side. Losing visibility
        // never discards it; only death, visible relocation, or a fully checked
        // empty footprint can do so.
        for(auto it=task.mainKnown.begin();it!=task.mainKnown.end();) {
            Unit now=game->getUnit(it->first);
            bool relocated=now && now->exists() && now->isVisible() && !inMainArea(task,now->getPosition());
            bool present=now && now->exists() && now->isVisible() && now->getType().isBuilding();
            bool gone=destroyedEnemies.count(it->first) || relocated || (!present && memoryFootprintVisible(it->second));
            if(gone) {
                log<<"{\"event\":\"main_building_cleared\",\"frame\":"<<frame<<",\"task_id\":"<<task.id<<",\"building_id\":"<<it->first
                   <<",\"type\":"<<it->second.type.getID()<<",\"reason\":\""<<(destroyedEnemies.count(it->first)?"destroyed":relocated?"relocated_outside":"footprint_checked")<<"\"}"<<std::endl;
                it=task.mainKnown.erase(it);
            } else ++it;
        }
        // Import earlier genuine sightings too, including buildings now in fog.
        for(const auto& e:remembered)if(!destroyedEnemies.count(e.first) && inMainArea(task,e.second.position) &&
            !task.mainKnown.count(e.first) && !memoryFootprintVisible(e.second))task.mainKnown.insert(e);
        task.mainUnchecked=0;Position survey=task.raidGoal;double surveyDistance=1e18;
        for(size_t i=0;i<task.mainSurvey.size();++i) {
            if(game->isVisible(TilePosition(task.mainSurvey[i])))task.mainChecked[i]=true;
            if(!task.mainChecked[i]) {
                ++task.mainUnchecked;double d=front.getDistance(task.mainSurvey[i]);
                if(d<surveyDistance){surveyDistance=d;survey=task.mainSurvey[i];}
            }
        }
        std::vector<CommanderMainArea::Building> candidates;
        for(const auto& e:task.mainKnown) {
            Unit now=game->getUnit(e.first);
            if(!now || !now->exists() || !now->isVisible())now=nullptr;
            bool reachable=std::any_of(units.begin(),units.end(),[&](Unit u){return now?
                u->canAttack(now) && (u->isFlying() || u->hasPath(e.second.position)):
                (u->isFlying() || u->hasPath(e.second.position)) && (u->getType().groundWeapon()!=WeaponTypes::None || u->getType()==Protoss_Carrier || u->getType()==Protoss_Reaver);});
            if(!reachable)continue;
            bool defensive=now && armed(now) && now->isCompleted() && (!now->getType().requiresPsi() || now->isPowered());
            candidates.push_back({e.first,e.second.type.isResourceDepot(),e.second.type==Protoss_Pylon,defensive,
                pylonValue(now,visibleBuildings),rallyPathDistance(front,e.second.position,false),now && now->isDetected()?health(now):1});
        }
        int chosen=CommanderMainArea::choose(candidates,task.mainTarget);
        std::string stage;
        if(chosen>=0) {
            const auto& m=task.mainKnown.at(chosen);task.goal=m.position;
            stage=m.type.isResourceDepot()?"main_core":m.type==Protoss_Pylon?"main_outer_pylon":"main_outer_building";
        } else if(!task.mainKnown.empty()) {task.goal=front;stage="main_blocked";}
        else if(localArmy) {task.goal=localArmy->getPosition();stage="main_combat";}
        else {task.goal=survey;stage=task.mainUnchecked?"main_surveying":"main_confirming";}
        if(chosen!=task.mainTarget || stage!=task.mainStage) {
            log<<"{\"event\":\"main_target_selected\",\"frame\":"<<frame<<",\"task_id\":"<<task.id<<",\"previous\":"<<task.mainTarget
               <<",\"target_id\":"<<chosen<<",\"type\":"<<(chosen>=0?task.mainKnown.at(chosen).type.getID():-1)
               <<",\"stage\":\""<<stage<<"\",\"goal\":["<<task.goal.x<<","<<task.goal.y<<"],\"known_buildings\":"<<task.mainKnown.size()
               <<",\"unchecked\":"<<task.mainUnchecked<<",\"power_value\":"<<(chosen>=0?pylonValue(enemy(chosen),visibleBuildings):0)<<"}"<<std::endl;
        }
        task.mainTarget=chosen;task.mainStage=stage;
        bool empty=task.mainKnown.empty() && !task.mainUnchecked && !localArmy && task.cloakPauses.empty() && !task.mainSurvey.empty();
        if(!empty)task.mainEmptySince=-1;else if(task.mainEmptySince<0)task.mainEmptySince=frame;
        if(CommanderMainArea::complete(int(task.mainKnown.size()),task.mainUnchecked,localArmy!=nullptr,!task.cloakPauses.empty(),
            task.mainEmptySince<0?0:frame-task.mainEmptySince))beginRaidWithdrawal(task,units,"main_area_cleared");
    }
    void finishRaid(BattleTask& task,const std::vector<Unit>& units) {
        if(task.action!=Plans::Action::Expansion && task.action!=Plans::Action::MainBase)return;
        if(task.mainArea){planMainArea(task,units);return;}
        if(task.action==Plans::Action::MainBase && task.raid.depot<0) {
            for(auto player:game->enemies())for(auto e:player->getUnits())if(live(e) && e->isVisible() &&
                e->getType().isResourceDepot() && e->getDistance(enemyStart)<512) {
                task.raid.depot=e->getID();task.raidGoal=e->getPosition();task.goal=task.raidGoal;
                task.raidTile=e->getTilePosition();task.raidType=e->getType();
                log<<"{\"event\":\"building_target_confirmed\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
                   <<",\"depot_id\":"<<task.raid.depot<<"}"<<std::endl;break;
            }
        }
        bool visible=task.raidTile.isValid();
        for(int x=0;x<task.raidType.tileWidth();++x)for(int y=0;y<task.raidType.tileHeight();++y)
            visible=visible && game->isVisible(task.raidTile+TilePosition(x,y));
        bool depotVisible=false,occupied=false;
        // Read this frame's visibility, not the eight-frame cached enemy list.
        for(auto player:game->enemies())for(auto e:player->getUnits())if(live(e) && e->isVisible()) {
            if(e->getID()==task.raid.depot)depotVisible=true;
            if(e->getType().isBuilding() && e->getDistance(task.raidGoal)<96)occupied=true;
        }
        if(!task.raid.finish(visible,depotVisible,occupied))return;
        task.action=Plans::Action::Retreat;task.phase="rally_withdrawing";task.clearSince=-1;
        task.targeting.clear();task.targetFacts.clear();task.focus.clear();task.flight.clear();task.escapes.clear();
        // Completion uses the common safe rendezvous controller: one fixed
        // point away from both the raided site and the main, then wait there.
        task.rendezvous=true;task.rallyFront=Positions::None;task.rallyInitial.clear();task.rallyFrontIds.clear();
        task.goal=home;
        for(auto u:units)task.rallyInitial.insert(u->getID());
        auto hazards=rallyHazards();prepareRally(task,units,hazards);
        for(auto u:units)if(Rally::frontUnit(point(home),point(task.rallyFront),point(task.goal),point(u->getPosition()),closeThreat(u,448)!=nullptr))
            task.rallyFrontIds.insert(u->getID());
        log<<"{\"event\":\"building_complete\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
           <<",\"reason\":\""<<(task.raid.destroyed?"depot_destroyed":"site_confirmed_empty")<<"\"";
        writeRaid(task);log<<"}"<<std::endl;
        taskState(task,units,0);
    }
    void baseAlert(int id,const char* kind,const std::wstring& message,int nearby) {
        baseNotice=message;noticeUntil=frame+240;
        const auto& watch=baseWatch.at(id);auto base=ours(id);
        log<<"{\"event\":\"base_notice\",\"frame\":"<<frame<<",\"kind\":\""<<kind<<"\",\"base_id\":"<<id
           <<",\"number\":"<<watch.number<<",\"nearby_army\":"<<nearby<<",\"message\":"<<CommanderHud::json(message);
        if(base)log<<",\"position\":["<<base->getPosition().x<<","<<base->getPosition().y<<"]";
        log<<"}"<<std::endl;
    }
    void observeBases() {
        std::set<int> alive;std::map<int,double> pressure;std::map<int,bool> air,attacked;
        for(auto b:defenceBases)alive.insert(b->getID());
        for(auto e:foes)if(live(e) && e->isVisible()) {
            if(e->getType().isWorker())continue; // Workers cannot pull the multi-base army allocation.
            Unit victim=e->getOrderTarget();if(!victim)victim=e->getTarget();
            bool hitting=live(victim) && victim->getPlayer()==game->self() &&
                (victim->getType().isBuilding() || victim->getType().isWorker()) &&
                (e->isAttacking() || e->isStartingAttack() || e->getOrder()==Orders::AttackUnit);
            if(!hitting && (!realThreat(e) && !combatEnemy(e)))continue;
            Unit nearest=nullptr;double distance=1e18;
            for(auto b:defenceBases) {
                double d=b->getDistance(hitting?victim:e);
                if(d<distance){distance=d;nearest=b;}
            }
            if(!nearest || e->getDistance(nearest)>CommanderDefence::zoneRadius)continue;
            BattleTask region;region.goal=nearest->getPosition();auto assets=protectedAssets(region);
            if(!hitting && e->getDistance(nearest)>384 && assetDistance(e,assets)>256)continue;
            pressure[nearest->getID()]+=strength(e);air[nearest->getID()]=air[nearest->getID()] || e->isFlying();
            attacked[nearest->getID()]=attacked[nearest->getID()] || hitting;
        }
        for(auto b:defenceBases) {
            int id=b->getID();bool first=!baseWatch.count(id);auto& watch=baseWatch[id];
            int nearby=int(std::count_if(army.begin(),army.end(),[&](Unit u){return live(u) && u->getDistance(b)<384;}));
            if(first) {
                watch.number=b->getDistance(home)>256?++expansionSerial:0;watch.complete=b->isCompleted();
                if(watch.number)baseAlert(id,watch.complete?"expansion_complete":"expansion_started",
                    L"分矿"+std::to_wstring(watch.number)+(watch.complete?L"建成":L"开建")+L"：附近"+std::to_wstring(nearby)+L"兵；0全军回防。",nearby);
            } else if(!watch.complete && b->isCompleted()) {
                watch.complete=true;
                if(watch.number)baseAlert(id,"expansion_complete",L"分矿"+std::to_wstring(watch.number)+L"建成：附近"+std::to_wstring(nearby)+L"兵。",nearby);
            }
            if(pressure[id]>0) {watch.pressure=pressure[id];watch.lastThreat=frame;watch.airThreat=air[id];}
            else if(frame-watch.lastThreat>96) {watch.pressure=0;watch.airThreat=false;}
            bool hit=attacked[id] || b->isUnderAttack();
            if(watch.number && hit && (!watch.underAttack || frame-watch.lastAlert>=480)) {
                watch.lastAlert=frame;
                bool defending=battleTasks.count(orders.mainTask) && battleTasks.at(orders.mainTask).action==Plans::Action::Defend;
                baseAlert(id,"expansion_attacked",L"分矿"+std::to_wstring(watch.number)+(defending?L"受袭：守军按敌情增援。":L"受袭：按0全军回防。"),nearby);
            }
            watch.underAttack=hit;
        }
        for(auto it=baseWatch.begin();it!=baseWatch.end();) {
            if(!alive.count(it->first)) {
                if(it->second.number)baseAlert(it->first,"expansion_lost",L"分矿"+std::to_wstring(it->second.number)+L"已消失，守军重新分配。",0);
                it=baseWatch.erase(it);
            } else ++it;
        }
    }
    void writeMultiBase(const BattleTask& task,const std::vector<Unit>& units) {
        std::map<int,int> counts;int waiting=0;
        for(auto u:units) {
            auto a=task.baseDefence.assigned.find(u->getID());
            if(a==task.baseDefence.assigned.end())++waiting;else ++counts[a->second];
        }
        log<<",\"defence_reinforced\":"<<(task.baseDefence.reinforced?"true":"false")<<",\"defence_waiting_route\":"<<waiting<<",\"base_distribution\":[";
        bool comma=false;for(const auto& r:task.defenceRegions)if(auto base=ours(r.first)) {
            if(comma)log<<",";comma=true;
            auto wanted=task.baseDefence.wanted.find(r.first);auto watch=baseWatch.find(r.first);
            log<<"{\"id\":"<<r.first<<",\"number\":"<<(watch==baseWatch.end()?0:watch->second.number)
               <<",\"position\":["<<base->getPosition().x<<","<<base->getPosition().y<<"],\"completed\":"<<(base->isCompleted()?"true":"false")
               <<",\"assigned\":"<<counts[r.first]<<",\"wanted\":"<<(wanted==task.baseDefence.wanted.end()?0:wanted->second)
               <<",\"pressure\":"<<(watch==baseWatch.end()?0:watch->second.pressure)<<"}";
        }
        log<<"]";
    }
    void controlMultiBase(BattleTask& task,const std::vector<Unit>& units) {
        bool changed=task.defenceRegions.size()!=defenceBases.size();
        for(auto b:defenceBases)if(!task.defenceRegions.count(b->getID()))changed=true;
        if(changed || task.lastBaseUnitCount!=int(units.size()) || frame-task.lastBasePlan>=24) {
            task.lastBasePlan=frame;task.lastBaseUnitCount=int(units.size());
            auto previous=task.baseDefence.assigned;std::vector<MultiBase::Base> sites;std::vector<MultiBase::Soldier> soldiers;
            Unit primary=nullptr;for(auto b:defenceBases)if(!primary || b->getDistance(home)<primary->getDistance(home))primary=b;
            for(auto b:defenceBases) {
                const auto& watch=baseWatch[b->getID()];
                sites.push_back({b->getID(),point(b->getPosition()),b==primary,watch.pressure,watch.airThreat});
                if(!task.defenceRegions.count(b->getID())) {
                    BattleTask region;region.id=task.id;region.key=task.key;region.scope=task.scope;region.action=Plans::Action::Defend;
                    region.goal=b->getPosition();region.regionalBase=b->getID();region.phase="returning";
                    task.defenceRegions.emplace(b->getID(),std::move(region));
                }
            }
            for(auto u:units) {
                MultiBase::Soldier s{u->getID(),point(u->getPosition()),strength(u),health(u),u->getType().airWeapon()!=WeaponTypes::None,{}};
                for(auto b:defenceBases)if(u->isFlying() || u->hasPath(b->getPosition()))s.reachable.insert(b->getID());
                soldiers.push_back(std::move(s));
            }
            task.baseDefence.update(sites,soldiers);
            for(auto it=task.defenceRegions.begin();it!=task.defenceRegions.end();) {
                if(std::none_of(defenceBases.begin(),defenceBases.end(),[&](Unit b){return b->getID()==it->first;}))it=task.defenceRegions.erase(it);else ++it;
            }
            bool moved=previous!=task.baseDefence.assigned;
            if(moved)for(auto u:units) {
                auto before=previous.find(u->getID()),after=task.baseDefence.assigned.find(u->getID());
                if(before==previous.end() || after==task.baseDefence.assigned.end() || before->second!=after->second)
                    for(auto& region:task.defenceRegions)region.second.flight.erase(u->getID());
            }
            if(moved || changed || frame%120==0) {
                log<<"{\"event\":\"base_allocation\",\"frame\":"<<frame<<",\"task_id\":"<<task.id
                   <<",\"multibase_policy\":\"proportional_reinforce_v1\",\"assignments\":[";
                bool comma=false;for(const auto& a:task.baseDefence.assigned){if(comma)log<<",";comma=true;log<<"["<<a.first<<","<<a.second<<"]";}
                log<<"]";writeMultiBase(task,units);log<<"}"<<std::endl;
            }
            if(primary)task.goal=primary->getPosition();
        }
        int arrived=0,engaged=0;bool reforming=false;std::string previous=task.phase;
        for(auto& r:task.defenceRegions) {
            r.second.id=task.id;r.second.scope=task.scope;r.second.key=task.key;
            std::vector<Unit> defenders;
            for(auto u:units)if(task.baseDefence.assigned.count(u->getID()) && task.baseDefence.assigned.at(u->getID())==r.first)defenders.push_back(u);
            if(defenders.empty())continue;
            controlTask(r.second,defenders,true);arrived+=r.second.reportedArrived;engaged+=r.second.reportedEngaged;
            reforming|=r.second.phase=="reforming";
            if(r.second.goal.getDistance(task.goal)<192)task.guardPost=r.second.guardPost;
        }
        for(auto u:units)if(!task.baseDefence.assigned.count(u->getID())) {
            task.flight.try_emplace(u->getID());battleOrder(u,task,u->getPosition(),nullptr,false,true);
        }
        task.phase=engaged?"intercepting":arrived<int(units.size())?"returning":reforming?"reforming":"guarding";
        if(frame%120==0 || task.phase!=previous)taskState(task,units,arrived);
    }
    void resetMembershipState(BattleTask& task,int id) {
        task.assaultMembers.erase(id);task.assaultJoining.erase(id);
        task.escapes.erase(id);task.flight.erase(id);task.targeting.erase(id);
        task.targetFacts.erase(id);task.focusChoices.erase(id);task.candidateCache.erase(id);
        task.standbySlots.erase(id);task.standbyGuards.erase(id);
        task.rallyInitial.erase(id);task.rallyFrontIds.erase(id);
        task.baseDefence.assigned.erase(id);task.lastBasePlan=-1000;task.standbyPlan=-1000;
        for(auto& region:task.defenceRegions)resetMembershipState(region.second,id);
    }
    void reinforceSquad() {
        if(!orders.formed)return;
        Position goal=battleTasks.count(orders.squadTask)?battleTasks.at(orders.squadTask).goal:enemyStart;
        auto candidates=army;
        std::sort(candidates.begin(),candidates.end(),[&](Unit a,Unit b){
            double da=a->getDistance(goal),db=b->getDistance(goal);
            return da==db?a->getID()<b->getID():da<db;
        });
        std::vector<Plans::Candidate> available;
        for(auto u:candidates)available.push_back({u->getID(),health(u)>=.7,u->getType()!=Protoss_Reaver});
        auto change=orders.reinforce(available);
        if(!change.changed())return;
        auto transferred=change.added;transferred.insert(change.returned.begin(),change.returned.end());
        transferred.insert(change.lost.begin(),change.lost.end());
        for(int id:transferred) {
            battleLastTask.erase(id);battleLastOrder.erase(id);
            for(auto& entry:battleTasks)resetMembershipState(entry.second,id);
        }
        log<<"{\"event\":\"squad_rebalance\",\"frame\":"<<frame
           <<",\"squad_policy\":\"dynamic_quarter_v1\",\"healthy_army\":"<<change.healthy
           <<",\"target\":"<<change.target<<",\"reserve_healthy\":"<<change.reserveHealthy
           <<",\"army\":"<<army.size()
           <<",\"squad_task\":"<<orders.squadTask<<",\"main_task\":"<<orders.mainTask<<",\"added\":";
        writeIds(change.added);log<<",\"returned\":";writeIds(change.returned);
        log<<",\"lost\":";writeIds(change.lost);
        log<<",\"squad_ids\":";writeIds(orders.squad);log<<"}"<<std::endl;
    }
    void controlBattle() {
        std::set<int> active;
        if(orders.mainTask)active.insert(orders.mainTask);
        if(orders.squadTask)active.insert(orders.squadTask);
        for(int id:active) {
            auto& task=battleTasks.at(id);auto units=taskUnits(id);
            if(units.empty()) {
                std::string phase=task.scope==Plans::Scope::Squad?"waiting_reinforcements":"waiting_units";
                if(frame%120==0 || task.phase!=phase){task.phase=phase;taskState(task,units,0);}
                continue;
            }
            if(task.action==Plans::Action::Defend)controlMultiBase(task,units);
            else controlTask(task,units);
        }
    }
    void controlTask(BattleTask& task,const std::vector<Unit>& units,bool regional=false) {
            int id=task.id;
            std::string previousPhase=task.phase;
            finishRaid(task,units);
            for(auto it=task.cloakPauses.begin();it!=task.cloakPauses.end();) {
                if(!detectionTask(task) || std::none_of(units.begin(),units.end(),[&](Unit u){return u->getID()==it->first;}))
                    it=task.cloakPauses.erase(it);
                else ++it;
            }
            if(task.stationary){controlAssembly(task,units);return;}
            if(task.action==Plans::Action::Clear) {
                Position target=clearEnemyGoal(task,units);
                if(target.isValid()) {
                    int seen=-1;for(auto e:foes)if(live(e) && e->isVisible() && e->getPosition()==target){seen=e->getID();break;}
                    task.encounter.observe(frame,point(target),seen);
                }
                task.encounter.update(frame,target.isValid());
                task.goal=target.isValid()?target:task.encounter.searching(frame)?
                    Position(int(task.encounter.lastKnown.x),int(task.encounter.lastKnown.y)):
                    Position(int(task.encounter.origin.x),int(task.encounter.origin.y));
                // Existing members keep their original posts. New members join
                // the same bounded area; no enemy worker supplies a destination.
                for(auto u:units)if(!task.standbySlots.count(u->getID()))
                    task.standbySlots[u->getID()]=reachablePoint(Position(int(task.encounter.origin.x),int(task.encounter.origin.y)),{u});
            }
            if(task.action==Plans::Action::Expansion || task.action==Plans::Action::MainBase) {
                for(auto u:units)if(task.assaultMembers.insert(u->getID()).second) {
                    task.assaultJoining.insert(u->getID());
                    log<<"{\"event\":\"assault_reinforcement\",\"frame\":"<<frame<<",\"task_id\":"<<id<<",\"unit_id\":"<<u->getID()<<",\"stage\":\"joining\"}"<<std::endl;
                }
                std::vector<Unit> core;
                for(auto u:units)if(!task.assaultJoining.count(u->getID()))core.push_back(u);
                if(core.empty()) {
                    auto front=*std::min_element(units.begin(),units.end(),[&](Unit a,Unit b){return a->getDistance(task.raidGoal)<b->getDistance(task.raidGoal);});
                    core.push_back(front);task.assaultJoining.erase(front->getID());
                }
                task.assaultJoinPoint=groupCenter(core);
                for(auto u:units)if(task.assaultJoining.count(u->getID()) &&
                    std::any_of(core.begin(),core.end(),[&](Unit v){return u->getDistance(v)<=160;})) {
                    task.assaultJoining.erase(u->getID());
                    log<<"{\"event\":\"assault_reinforcement\",\"frame\":"<<frame<<",\"task_id\":"<<id<<",\"unit_id\":"<<u->getID()<<",\"stage\":\"joined\"}"<<std::endl;
                }
            }
            if(task.action==Plans::Action::Defend && frame%24==0 && !defenceBases.empty() &&
                std::none_of(defenceBases.begin(),defenceBases.end(),[&](Unit b){return live(b) && b->getDistance(task.goal)<192;})) {
                task.goal=defendedBase();
                task.flight.clear();task.guardPost=Positions::None;task.lastGuardPlan=-1000;
                event("return_base_changed","previous_base_lost");
            }
            std::vector<Unit> assets;
            if(task.action==Plans::Action::Defend) {assets=protectedAssets(task);planDefence(task,units,assets);}
            if(task.action==Plans::Action::Gather && !cleared(task) && frame%24==0 && Rules::danger(point(task.goal),threats(true))>.2)task.goal=safeGather(units);
            bool pressure=std::any_of(units.begin(),units.end(),[&](Unit u){return closeThreat(u,448)!=nullptr;});
            if(task.action==Plans::Action::Retreat && !task.rendezvous) {
                if(pressure) {
                    task.clearSince=-1;
                    if(task.phase=="regrouping" || task.phase=="safe")task.phase="retreating";
                } else if(task.clearSince<0)task.clearSince=frame;
            }
            auto risks=threats(true);
            std::vector<Rally::Hazard> hazards;
            if(task.rendezvous){hazards=rallyHazards();prepareRally(task,units,hazards);}
            if(cleared(task))prepareStandby(task,units);
            prepareFocus(task,units);
            int arrived=0,engaged=0,slot=0,protectedCount=0,evadingCount=0,skirmishing=0;
            for(auto u:units) {
                if(cloakProtection(u,task)) {
                    ++protectedCount;if(task.cloakPauses.at(u->getID()).evading)++evadingCount;
                    skirmishing+=task.cloakPauses.at(u->getID()).safeTarget>=0;
                    continue;
                }
                Position goal=task.goal;Unit target=nullptr;bool attackMove=false,holdFire=false;
                if(cleared(task)) {
                    auto post=task.standbySlots.find(u->getID());
                    if(post==task.standbySlots.end()) {goal=u->getPosition();holdFire=true;}
                    else {
                        auto& guard=task.standbyGuards.at(u->getID());goal=post->second;
                        target=battleEnemy(u,task);
                        if(target) {guard.attack(frame,target->getID(),u->isInWeaponRange(target));++engaged;goal=target->getPosition();}
                        else {guard.noTarget(frame,point(u->getPosition()),point(post->second));holdFire=u->getPosition().getDistance(post->second)<=Standby::arrival;}
                        if(u->getPosition().getDistance(post->second)<=Standby::arrival)++arrived;
                    }
                } else if(task.rendezvous) {
                    rendezvousUnit(u,task,hazards);
                    const auto& f=task.flight.at(u->getID());goal=f.moveGoal;holdFire=f.hold;
                    if(f.resting)++arrived;
                } else if(task.action==Plans::Action::Retreat) {
                    auto& f=task.flight[u->getID()];Unit pursuer=closeThreat(u,800);
                    f.nearby=pursuer?pursuer->getID():-1;f.atBase=false;f.resting=false;
                    bool regroup=task.phase=="regrouping" || task.phase=="safe";
                    if(!task.escapes.count(u->getID()))task.escapes[u->getID()]=safeEscape(u);
                    auto& escape=task.escapes[u->getID()];
                    if(!regroup) {
                        bool reachedUnderPressure=u->getDistance(escape)<112 && closeThreat(u,400);
                        bool unsafe=frame-f.lastPlan>=48 && Rules::danger(point(escape),risks)>.2;
                        bool blocked=frame-f.lastPlan>=72 && u->isStuck();
                        if(reachedUnderPressure || unsafe || blocked) {escape=safeEscape(u,escape);f.lastPlan=frame;}
                    }
                    goal=regroup?task.goal:escape;
                    f.moveGoal=goal;
                    f.resting=u->getDistance(goal)<112 && !closeThreat(u,400);
                    if(f.resting)++arrived;
                    // A safe stationary hold cannot auto-acquire and chase the
                    // next enemy after a Move completes. Pressure resumes flight.
                    bool brief=briefFlightFire(u,task);
                    holdFire=f.resting || brief;
                    f.hold=holdFire;
                } else if(task.action==Plans::Action::Defend) {
                    auto& f=task.flight[u->getID()];Unit pursuer=closeThreat(u,800);
                    f.nearby=pursuer?pursuer->getID():-1;f.resting=false;f.intercepting=false;f.defendedAsset=-1;
                    f.atBase=CommanderDefence::arrived(f.atBase,u->getPosition().getDistance(task.goal),assetDistance(u,assets),u->getDistance(task.guardPost));
                    goal=defencePoint(task.guardPost+Position((u->getID()%7-3)*32,(u->getID()/7%7-3)*32),{u},assets);
                    f.moveGoal=goal;
                    if(f.atBase) {
                        if(f.fire.since>=0)f.fire.end(frame,point(u->getPosition()));
                        ++arrived;
                        Unit contact=nullptr;
                        if(f.guard.ready(frame,point(u->getPosition()),point(task.guardPost)))
                            contact=defenceEnemy(u,task,assets,f.defendedAsset);
                        if(contact && f.guard.allow(frame,point(u->getPosition()),point(task.goal),point(contact->getPosition()),contact->getID(),u->isInWeaponRange(contact))) {
                            f.intercepting=true;++engaged;
                            if(u->isInWeaponRange(contact)){target=contact;goal=u->getPosition();}
                            else if(health(u)>=.4) {
                                auto weapon=contact->isFlying()?u->getType().airWeapon():u->getType().groundWeapon();
                                double stand=std::max(32.0,double(u->getPlayer()->weaponMaxRange(weapon)+u->getType().width()/2+contact->getType().width()/2-8));
                                goal=reachablePoint(toward(contact->getPosition(),u->getPosition(),stand),{u});
                                if(goal.getDistance(task.goal)>CommanderDefence::zoneRadius)goal=reachablePoint(toward(task.goal,goal,CommanderDefence::zoneRadius-32),{u});
                                Position anchor(int(f.guard.origin.x),int(f.guard.origin.y));
                                if(goal.getDistance(anchor)>CommanderDefence::chaseDistance-16)goal=reachablePoint(toward(anchor,goal,CommanderDefence::chaseDistance-16),{u});
                                if(goal.getDistance(task.goal)>CommanderDefence::zoneRadius || goal.getDistance(anchor)>CommanderDefence::chaseDistance) {
                                    f.guard.recall(frame);f.intercepting=false;
                                    goal=reachablePoint(task.guardPost,{u});
                                }
                            } else {f.guard.recall(frame);f.intercepting=false;}
                        } else f.guard.noTarget(frame,point(u->getPosition()),point(task.guardPost));
                        if(!f.intercepting)holdFire=u->getDistance(goal)<64;
                        f.moveGoal=goal;
                    } else holdFire=briefFlightFire(u,task);
                    f.hold=holdFire;
                } else {
                    double radius=task.action==Plans::Action::Gather?128:160;
                    if(u->getDistance(task.goal)<radius)++arrived;
                    target=battleEnemy(u,task);
                    if(target){++engaged;goal=target->getPosition();}
                    else if(task.action==Plans::Action::Gather) {
                        goal=reachablePoint(task.goal+Position((slot%5-2)*24,(slot/5%5-2)*24),{u});++slot;
                    } else if(task.action==Plans::Action::Clear) {
                        bool search=task.encounter.emptySince>=0 && task.encounter.searching(frame) &&
                            task.goal.getDistance(patrolPost(task,u))<=CommanderClear::radius;
                        goal=reachablePoint(search?task.goal:task.standbySlots.at(u->getID()),{u});holdFire=u->getDistance(goal)<64;
                    } else if(task.targeting[u->getID()].advanceOnly) {
                        goal=reachablePoint(task.assaultJoining.count(u->getID())?task.assaultJoinPoint:task.goal,{u});
                        holdFire=u->getDistance(goal)<96;
                    } else if(task.action==Plans::Action::Expansion || task.action==Plans::Action::MainBase) {
                        // Plain movement cannot auto-acquire fleeing workers,
                        // unrelated structures, or enemies near the main base.
                        goal=reachablePoint(task.assaultJoining.count(u->getID())?task.assaultJoinPoint:task.mainArea?task.goal:task.raidGoal,{u});
                        holdFire=u->getDistance(goal)<64;
                    } else attackMove=true;
                }
                battleOrder(u,task,goal,target,attackMove,holdFire);
                if(frame%120==0) {
                    auto p=u->getPosition();auto cmd=u->getLastCommand();auto targetPosition=cmd.getTargetPosition();
                    Unit nearby=closeThreat(u,800);double baseDistance=1e18;
                    for(auto base:bases)baseDistance=std::min(baseDistance,double(u->getDistance(base)));
                    log<<"{\"event\":\"battle_sample\",\"schema\":4,\"frame\":"<<frame<<",\"task_id\":"<<id<<",\"unit_id\":"<<u->getID()
                       <<",\"action\":\""<<Plans::actionName(task.action)<<"\",\"position\":["<<p.x<<","<<p.y<<"],\"goal\":["<<goal.x<<","<<goal.y
                       <<"],\"last_target\":["<<targetPosition.x<<","<<targetPosition.y<<"],\"command_type\":"<<cmd.getType().getID()
                       <<",\"threat_distance\":"<<(nearby?u->getDistance(nearby):-1)<<",\"nearest_base_distance\":"<<baseDistance;
                    if(task.action==Plans::Action::Retreat || task.action==Plans::Action::Defend)writeFlight(u,task);
                    if(cleared(task))writeStandby(u,task,target);
                    writeRaid(task);
                    log<<"}"<<std::endl;
                }
            }
            if(cleared(task)) {
                bool returning=std::any_of(task.standbyGuards.begin(),task.standbyGuards.end(),[](const auto& entry){return entry.second.returning;});
                task.phase=engaged?"standby_engaging":task.standbySlots.size()<units.size()?"standby_blocked":returning?"standby_returning":
                    arrived==int(units.size())?"standby_waiting":"standby_forming";
            } else if(task.rendezvous) {
                bool withdrawing=false,frontReady=true;
                for(auto u:units) {
                    const auto& f=task.flight.at(u->getID());
                    withdrawing|=f.rallyStage=="evading";
                    if(task.rallyFrontIds.count(u->getID()) && !f.resting)frontReady=false;
                }
                bool waiting=arrived>0 && (frontReady || arrived>=int((units.size()+1)/2));
                task.phase=withdrawing?"rally_withdrawing":waiting?"rally_waiting":"rally_joining";
            } else if(task.action==Plans::Action::Retreat && arrived==int(units.size()) && !pressure && task.clearSince>=0 && frame-task.clearSince>=72) {
                if(task.phase=="retreating") {task.phase="regrouping";task.goal=safeGather(units);}
                else task.phase="safe";
            } else if(task.action==Plans::Action::Clear)task.phase=engaged?"patrol_engaging":
                task.encounter.emptySince>=0 && task.encounter.searching(frame)?"patrol_searching":"patrol_waiting";
            else if(task.action==Plans::Action::Gather)task.phase=arrived==int(units.size())?"holding":"gathering";
            else if(task.action==Plans::Action::Defend)task.phase=engaged?"intercepting":arrived<int(units.size())?"returning":
                std::any_of(units.begin(),units.end(),[&](Unit u){return task.flight.at(u->getID()).guard.returning;})?"reforming":"guarding";
            else if(task.mainArea && task.action==Plans::Action::MainBase) {
                bool combat=std::any_of(units.begin(),units.end(),[&](Unit u){
                    auto t=task.targetFacts.find(u->getID());
                    return t!=task.targetFacts.end() && enemy(t->second.id) && (!t->second.building || t->second.armed);
                });
                task.phase=combat?"main_combat":engaged?task.mainStage:
                    (task.mainStage=="main_surveying" || task.mainStage=="main_confirming" || task.mainStage=="main_blocked")?task.mainStage:"main_advancing";
            }
            else if(task.action!=Plans::Action::Retreat)task.phase=engaged?"engaging":"advancing";
            if(protectedCount)task.phase=skirmishing?"detection_skirmishing":evadingCount?"evading_cloak":"waiting_detection";
            task.reportedArrived=arrived;task.reportedEngaged=engaged;
            if(!regional && (frame%120==0 || task.phase!=previousPhase))taskState(task,units,arrived);
    }
    void testInput() {
        if (testCommands.empty()) return;
        std::ifstream file(testCommands);int sequence=0,key=0;std::string token;
        if(!(file>>sequence>>token))return;
        if(token=="-1")key=-1;
        else if(token.size()==1)key=CommanderKeys::shortcut(token[0]=='-'?CommanderKeys::topRowMinus:token[0],false,false,false);
        else return;
        if(!key)return;
        if (sequence>lastTestSequence) {
            lastTestSequence=sequence;
            if(key==-1){testStop=true;game->leaveGame();}else keys.push_back(key);
        }
    }
    std::wstring hudTask(int id,bool squad) const {
        auto it=battleTasks.find(id);
        if(it==battleTasks.end())return squad?(orders.formed?L"自动作战":L"未编组"):L"自动作战";
        if(it->second.action==Plans::Action::Defend && it->second.phase!="waiting_units" && it->second.phase!="lost")
            return it->second.baseDefence.reinforced?L"增援防守":it->second.phase=="returning"?L"多地回防":L"多基地防守";
        if(it->second.raid.withdrawing)return it->second.phase=="safe" || it->second.phase=="rally_waiting"?L"拆除后待命":L"拆除后撤离";
        if(cleared(it->second) && it->second.phase!="lost" && it->second.phase!="waiting_units")return it->second.phase=="standby_engaging"?L"就近迎敌":it->second.phase=="standby_waiting"?L"空地待命":
            it->second.phase=="standby_blocked"?L"等待空位":it->second.phase=="standby_returning"?L"收回站位":L"空地展开";
        return CommanderHud::task(it->second.action,it->second.phase);
    }
    void drawChineseHud(bool manual,bool commandPending) {
        CommanderHud::Data data;
        data.mode=orders.selected;data.selected=int(orders.selected==Plans::Scope::Squad?orders.squad.size():army.size());
        data.army=int(army.size());data.miningLocked=miningLocked;data.manualCamera=manual;data.status=feedback;data.scoutStatus=evadeStatus;
        data.mainTask=hudTask(orders.mainTask,false);data.squadTask=hudTask(orders.squadTask,true);
        data.lastKey=hudLastKey;data.lastSafeRally=hudLastSafeRally;data.lastMode=hudLastMode;data.commandUnits=hudCommandUnits;data.reserves=hudReserves;
        if(frame<=noticeUntil && frame-lastHudCommand>72)data.notice=baseNotice;
        auto lines=CommanderHud::lines(data);size_t draws=0;int maxWidth=0;
        LARGE_INTEGER start{},stop{},frequency{};QueryPerformanceCounter(&start);
        bool supported=hud.ready();
        for(size_t row=0;row<lines.size() && supported;++row) {
            const auto& raster=hud.render(row,lines[row]);maxWidth=std::max(maxWidth,raster.width);
            int left=6,top=CommanderHud::Raster::top+int(row)*CommanderHud::Raster::pitch;
            auto color=row==2?Colors::Cyan:row==3?Colors::Yellow:Colors::White;
            const Color shades[]={Colors::Black,
                Color(color.red()/3,color.green()/3,color.blue()/3),
                Color(color.red()*2/3,color.green()*2/3,color.blue()*2/3),color};
            auto stroke=[&](int x1,int y,int x2,Color ink) {
                if(x1==x2)game->drawDotScreen(x1,y,ink);
                else game->drawLineScreen(x1,y,x2,y,ink);
            };
            // Paint the entire black outline first to retain narrow counters
            // and foreground strokes in small Chinese glyphs.
            for(const auto& run:raster.outline)stroke(left+run.left,top+run.y,left+run.right-1,Colors::Black);
            for(const auto& run:raster.runs)stroke(left+run.left,top+run.y,left+run.right-1,shades[run.shade]);
            draws+=raster.runs.size()+raster.outline.size();
        }
        QueryPerformanceCounter(&stop);QueryPerformanceFrequency(&frequency);
        if(commandPending || frame%120==0) {
            log<<"{\"event\":\"hud\",\"frame\":"<<frame<<",\"language\":\"zh-CN\",\"font_supported\":"<<(supported?"true":"false")
               <<",\"raster_policy\":\"grayscale_cjk_v1\",\"font_face\":"<<CommanderHud::json(hud.fontFace())
               <<",\"font_pixels\":13,\"origin\":[6,16],\"line_pitch\":18"
               <<",\"draw_calls\":"<<draws<<",\"max_text_width\":"<<maxWidth
               <<",\"draw_microseconds\":"<<(frequency.QuadPart?double(stop.QuadPart-start.QuadPart)*1e6/frequency.QuadPart:0)
               <<",\"last_key\":\""<<(hudPhysicalKey?std::string(1,hudPhysicalKey):std::string())<<"\",\"feedback\":\""<<feedback<<"\",\"lines\":[";
            bool comma=false;for(const auto& line:lines){if(comma)log<<",";comma=true;log<<CommanderHud::json(line);}
            log<<"]}"<<std::endl;
        }
    }
    void recordAutomaticControl() {
        for(auto it=autoSince.begin();it!=autoSince.end();) {
            Unit u=ours(it->first);
            if(!u || orders.taskFor(it->first) || evadeId==it->first) {
                autoObserved.erase(it->first);it=autoSince.erase(it);continue;
            }
            auto observed=autoBefore.find(it->first);
            if(observed!=autoBefore.end() && (!autoObserved.count(it->first) || frame%120==0)) {
                auto before=observed->second,after=u->getLastCommand();auto p=after.getTargetPosition();
                bool untouched=before.getType()==after.getType() && before.getTarget()==after.getTarget() &&
                    before.getTargetPosition()==after.getTargetPosition() && u->getLastCommandFrame()==frame;
                log<<"{\"event\":\"auto_control\",\"frame\":"<<frame<<",\"unit_id\":"<<it->first
                   <<",\"released_at\":"<<it->second<<",\"kind\":\""<<(u->getType().isWorker()?"probe":"army")
                   <<"\",\"command_type\":"<<after.getType().getID()<<",\"target\":["<<p.x<<","<<p.y
                   <<"],\"baseline_untouched\":"<<(untouched?"true":"false")<<"}"<<std::endl;
                autoObserved.insert(it->first);
            }
            ++it;
        }
    }
public:
    void onStart() override {
        role=env("COMMANDER_ROLE","human");human=role=="human" && env("COMMANDER_CONTROL","human")=="human";
        smokeLimit=std::atoi(env("COMMANDER_SMOKE_FRAMES","0").c_str());
        testCommands=env("COMMANDER_TEST_COMMANDS");
        dataOnly=env("COMMANDER_DATA_ONLY")=="1";
        if(role=="human" && env("COMMANDER_RESULTS_WATCH")=="1") {
            const auto config=env("BWAPI_CONFIG_INI");
            if(!config.empty())WritePrivateProfileStringA("auto_menu","auto_menu","OFF",config.c_str());
        }
        log.open("bwapi-data/write/commander-layer.jsonl");
        if(env("COMMANDER_DIAGNOSTICS")=="1" || !env("COMMANDER_NETWORK_SCOPE").empty())
            diagnosticLog.open("bwapi-data/write/delegate-diagnostic.jsonl");
        library=LoadLibraryA(env("COMMANDER_DELEGATE","bwapi-data/AI/Baseline.dll").c_str());
        auto init=library?reinterpret_cast<void(*)(Game*)>(GetProcAddress(library,"gameInit")):nullptr;
        auto create=library?reinterpret_cast<AIModule*(*)()>(GetProcAddress(library,"newAIModule")):nullptr;
        if (!init || !create) {event("load_error",std::to_string(GetLastError()));game->leaveGame();return;}
        init(game);delegate=create();diagnosticStage("start_enter");delegate->onStart();diagnosticStage("start_return");
        if(dataOnly)game->setGUI(false); // Native simulation, test-only rendering suppression.
        PlayerColors::apply(game);MatchIdentity::save(game);
        if (role == "human") {
            cameraLog.open("bwapi-data/write/camera-track.jsonl", std::ios::trunc);
            cameraLog << "{\"event\":\"camera_start\",\"schema\":1,\"player_id\":"
                      << game->self()->getID() << ",\"viewport\":[" << NativeViewport::size().x << "," << NativeViewport::size().y << "]}\n";
            recordCamera(0, true);
        }
        home=Position(game->self()->getStartLocation())+Position(64,48);enemyStart=home;
        for(auto t:game->getStartLocations())if(t!=game->self()->getStartLocation())enemyStart=Position(t)+Position(64,48);

        if (human) {
            BWEM::Map::Instance().Initialize(game);BWEM::Map::Instance().FindBasesForStartingLocations();
            keys.clear();std::fill_n(commandHeld,11,false);lastInput=GetTickCount64();mousePoint=-1;
            hook=SetWindowsHookExW(WH_GETMESSAGE,input,nullptr,GetCurrentThreadId());
        }
        log<<"{\"event\":\"start\",\"frame\":0,\"role\":\""<<role<<"\",\"human_layer\":"<<(human?"true":"false")
           <<",\"delegate_loaded\":true,\"controls\":\"toprow-commands-v17\",\"viewport\":["
           <<NativeViewport::size().x<<","<<NativeViewport::size().y<<"],\"keymap\":\"toprow_commands_v17\",\"squad_policy\":\"dynamic_quarter_v1\"}"<<std::endl;
        HWND window=nullptr;EnumWindows(findGameWindow,reinterpret_cast<LPARAM>(&window));
        std::ofstream("bwapi-data/write/game-window.txt")<<reinterpret_cast<uintptr_t>(window);
    }
    void onFrame() override {
        if (!delegate || ended) return;
        frame=game->getFrameCount();
        if(smokeLimit>0 && frame>=smokeLimit){game->leaveGame();return;}
        if(lastTick==frame)return;lastTick=frame;
        if(human) {
            if(miningLocked && !attachMiningGuards()){miningLocked=false;ProbeCommandGuard::releaseAll(this);feedback="mining_unavailable";}
            renewScoutOwnership();renewManualOrders();renewObservers();
        }
        bool diagnoseFrame=frame<240 || frame%120==0;
        if(diagnoseFrame)diagnosticStage("frame_enter");
        delegateRunning=true;delegate->onFrame();delegateRunning=false;
        if(diagnoseFrame)diagnosticStage("frame_return");
        if(frame%8==0){refresh();if(human)observeBases();}
        if(human) {
            autoBefore.clear();
            for(const auto& entry:autoSince)if(auto u=ours(entry.first))
                if(u->getLastCommandFrame()==frame && frame>entry.second)autoBefore.emplace(entry.first,u->getLastCommand());
            observeScouts();
            testInput();if(testStop)return;bool commandPending=!keys.empty();
            if(commandPending){refresh();observeBases();}applyKeys();
            if(frame%8==0 || commandPending)reinforceSquad();
            controlBattle();controlObservers();
            // Plan rescue after fresh observations. The pre-delegate renewal
            // prevents two competing native moves from entering the same turn.
            protectScout();
            controlMining();
            recordAutomaticControl();
            DWORD foreground=0;GetWindowThreadProcessId(GetForegroundWindow(),&foreground);
            if(foreground==GetCurrentProcessId() && (GetAsyncKeyState(VK_LBUTTON)&0x8000 || GetAsyncKeyState(VK_RBUTTON)&0x8000 ||
                GetAsyncKeyState(VK_LEFT)&0x8000 || GetAsyncKeyState(VK_RIGHT)&0x8000 || GetAsyncKeyState(VK_UP)&0x8000 || GetAsyncKeyState(VK_DOWN)&0x8000))lastInput=GetTickCount64();
            bool manual=GetTickCount64()-lastInput<10000;
            if(!manual && foreground==GetCurrentProcessId() && !game->isPaused() && frame%24==0 && lastCamera!=frame) {
                lastCamera=frame;game->setScreenPosition(clamp(center()-NativeViewport::cameraOffset()));
            }
            if(!dataOnly)drawChineseHud(manual,commandPending);
        }
        recordCamera(frame);
        if(frame%120==0) {
            if(human && !testCommands.empty()) {
                log<<"{\"event\":\"validation_native\",\"frame\":"<<frame<<",\"units\":[";
                bool comma=false;
                for(auto player:game->getPlayers())if(!player->isNeutral())for(auto u:player->getUnits())if(u && u->exists() && (player==game->self() || u->isVisible())) {
                    if(comma)log<<",";comma=true;auto target=u->getOrderTarget();auto pos=u->getPosition();
                    log<<"{\"id\":"<<u->getID()<<",\"self\":"<<(player==game->self()?"true":"false")
                       <<",\"type\":"<<u->getType().getID()<<",\"position\":["<<pos.x<<","<<pos.y<<"]"
                       <<",\"order\":"<<u->getOrder().getID()<<",\"target\":"<<(target?target->getID():-1)
                       <<",\"hp\":"<<u->getHitPoints()<<",\"shield\":"<<u->getShields()
                       <<",\"detected\":"<<(u->isDetected()?"true":"false")<<",\"cloaked\":"<<(u->isCloaked()?"true":"false")
                       <<",\"completed\":"<<(u->isCompleted()?"true":"false")<<",\"sight\":"<<u->getPlayer()->sightRange(u->getType())
                       <<",\"powered\":"<<(u->isPowered()?"true":"false")
                       <<",\"attacking\":"<<(u->isAttacking()?"true":"false")<<",\"cooldown\":"<<u->getGroundWeaponCooldown()
                       <<",\"invincible\":"<<(u->isInvincible()?"true":"false")<<"}";
                }
                log<<"]}"<<std::endl;
            }
            Position c=center();
            log<<"{\"event\":\"state\",\"frame\":"<<frame<<",\"schema\":6,\"keymap\":\"toprow_commands_v17\",\"stance\":\""<<(human?taskLabel(orders.mainTask):"baseline")
               <<"\",\"mode\":\""<<Plans::scopeName(orders.selected)<<"\",\"workers\":"
               <<std::count_if(game->self()->getUnits().begin(),game->self()->getUnits().end(),[](Unit u){return u->getType().isWorker();})
               <<",\"completed_workers\":"<<std::count_if(game->self()->getUnits().begin(),game->self()->getUnits().end(),[](Unit u){return u->getType().isWorker() && u->isCompleted();})
               <<",\"army\":"<<army.size()<<",\"main_army\":"<<mainArmy().size()<<",\"center\":["<<c.x<<","<<c.y<<"]"
               <<",\"healthy_army\":"<<std::count_if(army.begin(),army.end(),[&](Unit u){return health(u)>=.7;})
               <<",\"main_task\":"<<orders.mainTask<<",\"squad_task\":"<<orders.squadTask<<",\"squad_formed\":"<<(orders.formed?"true":"false")
               <<",\"squad_ids\":";writeIds(orders.squad);
            log<<",\"mining_lock\":"<<(miningLocked?"true":"false")<<",\"mining_blocked\":"<<miningBlocked
               <<",\"mining_guards\":"<<ProbeCommandGuard::count(this)<<",\"gathered_minerals\":"<<game->self()->gatheredMinerals()
               <<",\"gathered_gas\":"<<game->self()->gatheredGas()<<",\"scout\":\""<<scoutStatus<<"\",\"evade\":\""<<evadeStatus<<"\",\"probe_id\":"<<evadeId
               <<",\"evade_overrides\":"<<evadeOverrides<<",\"battle_overrides\":"<<battleOverrides<<"}"<<std::endl;
        }
    }
    void onEnd(bool winner) override {
        if(ended)return;ended=true;
        ProbeCommandGuard::releaseAll(this);
        hud.reset();
        recordCamera(game->getFrameCount(), true);
        if (cameraLog.is_open()) {
            cameraLog << "{\"event\":\"camera_end\",\"frame\":" << game->getFrameCount() << "}\n";
            cameraLog.close();
        }
        if(hook){UnhookWindowsHookEx(hook);hook=nullptr;}
        if(delegate)delegate->onEnd(winner);
        if(role=="human" && env("COMMANDER_RESULTS_WATCH")=="1") {
            HMODULE reporter=LoadLibraryA("bwapi-data/AI/ResultsWatcher.dll");
            auto start=reporter?reinterpret_cast<void(__cdecl*)()>(GetProcAddress(reporter,"StartResultsWatcher")):nullptr;
            if(start)start();
        }
        log<<"{\"event\":\"end\",\"frame\":"<<game->getFrameCount()<<",\"winner\":"<<(winner?"true":"false")<<"}"<<std::endl;
        log.close();std::ofstream("bwapi-data/write/match-ended.json")<<"{\"frame\":"<<game->getFrameCount()<<",\"winner\":"<<(winner?"true":"false")<<"}";
    }
    void onSendText(std::string text) override {if(delegate)delegate->onSendText(text);}
    void onReceiveText(Player p,std::string text) override {if(delegate)delegate->onReceiveText(p,text);}
    void onPlayerLeft(Player p) override {if(delegate)delegate->onPlayerLeft(p);}
    void onNukeDetect(Position p) override {if(delegate)delegate->onNukeDetect(p);}
    void onSaveGame(std::string name) override {if(delegate)delegate->onSaveGame(name);}
    void onUnitDestroy(Unit u) override {
        ProbeCommandGuard::release(u);
        if(u && game->self()->isEnemy(u->getPlayer()))destroyedEnemies.insert(u->getID());
        if(human && u)for(auto& entry:battleTasks) {
            entry.second.raid.lost(u->getID());entry.second.encounter.destroyed(u->getID());
        }
        if(delegate)delegate->onUnitDestroy(u);
    }
#define FORWARD(name) void name(Unit u) override {if(delegate)delegate->name(u);}
    FORWARD(onUnitDiscover) FORWARD(onUnitEvade) FORWARD(onUnitShow) FORWARD(onUnitHide)
    FORWARD(onUnitCreate) FORWARD(onUnitMorph) FORWARD(onUnitRenegade) FORWARD(onUnitComplete)
#undef FORWARD
};
extern "C" void gameInit(Game* g){game=g;BroodwarPtr=g;}
extern "C" AIModule* newAIModule(){return new CommanderLayer();}
