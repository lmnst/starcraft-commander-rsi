#pragma once
#include "CommanderRules.h"

namespace CommanderRaid {
// The building is the strategic destination, not an exclusion of route combat.
// Mobile pursuit is bounded by CommanderTargeting's fixed engagement anchor.
inline bool permits(bool objective,bool activeThreat,bool inRange,bool /*stationary*/,double /*fromRaid*/,
                    bool combat=false,bool worker=false) {
    if(objective)return true;
    if(worker)return activeThreat && inRange;
    return combat;
}
inline bool safeWithdrawal(CommanderRules::Point destination,CommanderRules::Point raid,
                           CommanderRules::Point enemyMain,CommanderRules::Point home) {
    if(CommanderRules::distance(destination,home)<384)return true;
    return CommanderRules::distance(destination,raid)>=512 &&
           CommanderRules::distance(destination,enemyMain)>=512;
}
// Fog is never evidence of success. A raid owns one depot identity for its
// entire lifetime, including the withdrawal inherited by a split squad.
struct Objective {
    int depot=-1;
    bool destroyed=false,withdrawing=false,expectedSite=false;
    void lost(int id) { if(depot>=0 && depot==id)destroyed=true; }
    bool finish(bool footprintVisible,bool depotVisible,bool siteOccupied) {
        if((depot<0 && !expectedSite) || withdrawing)return false;
        if(!destroyed && (!footprintVisible || depotVisible || siteOccupied))return false;
        withdrawing=true;
        return true;
    }
};
}
