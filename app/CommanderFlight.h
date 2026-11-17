#pragma once
#include "CommanderRules.h"

namespace CommanderFlight {
// One short, stationary firing opportunity followed by a mandatory run. The
// engine gets Hold_Position, never Attack_Unit, so it cannot pursue a target.
constexpr int fireBudget=10, runFrames=72;
constexpr double runDistance=96, closeDistance=96, maxDrift=12;
inline bool opportunity(bool mobileCombatEnemy, bool inWeaponRange, int cooldown,
                        double health, double distance, int nearbyEnemies) {
    return mobileCombatEnemy && inWeaponRange && cooldown==0 && health>=.55 &&
           distance<=closeDistance && nearbyEnemies<=2;
}
inline bool baseArrival(bool alreadyArrived,double distance) {
    return distance<=(alreadyArrived?224:160);
}
struct Window {
    int target=-1,since=-1,lastEnd=-10000;
    bool hasRunOrigin=false;
    CommanderRules::Point start{0,0},runOrigin{0,0};
    int bursts=0;
    bool canStart(int frame,CommanderRules::Point position) const {
        return since<0 && frame-lastEnd>=runFrames &&
            (!hasRunOrigin || CommanderRules::distance(position,runOrigin)>=runDistance);
    }
    void begin(int frame,CommanderRules::Point position,int enemy) {
        since=frame;start=position;target=enemy;++bursts;
    }
    bool keepFiring(int frame,CommanderRules::Point position,bool stillClose,bool fired) const {
        return since>=0 && frame-since<fireBudget && stillClose && !fired &&
            CommanderRules::distance(position,start)<=maxDrift;
    }
    void end(int frame,CommanderRules::Point position) {
        since=-1;target=-1;lastEnd=frame;runOrigin=position;hasRunOrigin=true;
    }
};
}
