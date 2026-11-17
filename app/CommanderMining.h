#pragma once
#include "CommanderRules.h"
namespace CommanderMining {
// Baseline builders move to tileOrigin+(32,32); scouting/worker combat is not
// a construction task. Preserve that build approach without permitting an old
// enemy-bound scout route. Build/Gather/ReturnCargo pass through unchanged.
inline bool builderApproach(int x,int y,double toEnemyMain) {
    return x>=32 && y>=32 && x%32==0 && y%32==0 && toEnemyMain>640;
}
inline bool preserveBuild(int frame,int expires,bool structureStarted,bool constructing) {
    return constructing || (!structureStarted && frame<=expires);
}
}
