#include "../app/CommanderAssembly.h"
#include "../app/CommanderDetection.h"
#include "../app/CommanderKeys.h"
#include <cassert>
int main() {
    using namespace CommanderAssembly;
    assert(boundedStep(100,384,false));
    assert(!boundedStep(380,450,false)); // Repeated local dodges cannot drift outside the original area.
    assert(boundedStep(900,750,true));
    assert(!boundedStep(900,950,true));
    Progress progress;
    assert(!progress.stalled(0,{0,0},{100,0}));
    assert(!progress.stalled(119,{0,0},{100,0}));
    assert(progress.stalled(120,{0,0},{100,0}));
    assert(!progress.stalled(121,{60,0},{100,0})); // Arrival stops stall handling.
    assert(preservePost(128) && !preservePost(512));
    assert(returnFire(false,true,true,true));
    assert(!returnFire(true,true,true,true)); // Ignore a worker even firing.
    assert(!returnFire(false,false,true,true));
    assert(!returnFire(false,true,true,false)); // Never approach for a shot.
    assert(!returnFire(false,true,false,true)); // Ordinary buildings ignored.
    assert(!localEscape(0,true,true,true));
    assert(!localEscape(.5,false,false,false)); // No default withdrawal.
    assert(localEscape(.5,true,false,false));
    assert(CommanderDetection::protects(8,false,false,true));
    assert(!CommanderDetection::protects(8,false,false,false));
    assert(CommanderKeys::shortcut('8',false,false,false)=='8');
    assert(!CommanderKeys::shortcut('8',false,false,true));
    assert(!CommanderKeys::shortcut('8',true,false,true));
    assert(!CommanderKeys::shortcut('8',false,true,false));
    assert(!CommanderKeys::shortcut('9',false,false,true));
}
