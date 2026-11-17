#include "../app/CommanderRules.h"
#include <cassert>
using namespace CommanderRules;
int main() {
    // Existing enemy-bound Probe accepted; army, miners and builders rejected.
    assert(scoutProbe(true,true,false,false,false,true,100));
    assert(scoutProbe(true,true,false,false,true,false,1000));
    assert(!scoutProbe(false,true,false,false,true,true,100));
    assert(!scoutProbe(true,true,true,false,true,true,100));
    assert(scoutProbe(true,true,false,true,true,true,100)); // scout carrying old cargo
    assert(!scoutProbe(true,true,false,true,false,false,100)); // ordinary miner
    assert(!scoutProbe(true,true,false,false,false,false,100));
    assert(!scoutProbe(true,true,false,false,false,true,800));
    assert(rescueHome(.4,0,false));
    assert(rescueHome(.9,2,false));
    assert(rescueHome(.9,0,true));
    assert(!rescueHome(.9,1,false));
    assert(rescueMoveNeeded(false,0,false));  // baseline switched to gather/attack
    assert(rescueMoveNeeded(true,500,false)); // baseline switched to enemy base
    assert(!rescueMoveNeeded(true,0,false)); // matching rescue order stays intact
}
