#pragma once
#include "CommanderRules.h"

namespace CommanderClear {
constexpr double radius=384;
constexpr int clearFrames=72;
inline bool target(bool worker,bool building,bool combat,bool /*activeThreat*/,double fromOrigin,bool defensiveBuilding=false) {
    return !worker && fromOrigin<=radius && (building?defensiveBuilding:combat);
}
inline bool approach(bool /*stationary*/,bool inRange,bool /*fleeing*/,double distance) {
    return inRange || distance<=400;
}
struct Encounter {
    CommanderRules::Point origin{0,0};
    int emptySince=-1;
    bool finished=false;
    CommanderRules::Point lastKnown{0,0};
    int lastSeen=-10000;
    int lastEnemy=-1;
    void observe(int frame,CommanderRules::Point position,int enemy=-1) {lastKnown=position;lastSeen=frame;lastEnemy=enemy;}
    void destroyed(int id) {if(lastEnemy==id){lastSeen=-10000;lastEnemy=-1;}}
    bool searching(int frame) const {return frame-lastSeen<=96;}
    bool update(int frame,bool enemyPresent) {
        if(enemyPresent){emptySince=-1;return false;}
        if(emptySince<0)emptySince=frame;
        // An empty/fogged area is a waiting state, never task completion.
        return false;
    }
};
}
