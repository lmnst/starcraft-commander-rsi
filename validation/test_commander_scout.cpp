#include "../app/CommanderScout.h"
#include <cassert>
#include <iostream>
using namespace CommanderScout;
int main() {
    // The historical 24-frame old-scout cycle is suppressed before enqueueing;
    // other units' own last-command frames are never touched by this policy.
    int last=3357,competing=0,renewals=0;
    for(int frame=3358;frame<=4104;++frame) {
        if(renew(frame,last,3365,true)){last=frame;++renewals;}
        if(frame%24==0 && frame-last>=16)++competing;
    }
    assert(renewals>80 && competing==0);
    assert(renew(30,29,0,false)); // Immediate correction after foreign command.
    assert(!renew(30,29,0,true)); // No continuous reissue outside settling.
    Progress p;p.reset(3432,{1287,3672});
    for(int frame=3456;frame<=3504;frame+=24)
        assert(!p.stalled(frame,frame%48?CommanderRules::Point{1287,3672}:CommanderRules::Point{1311,3692},300));
    assert(p.stalled(3528,{1287,3672},300)); // 31px oscillation is not escape.
    p.reset(100,{100,100});
    assert(!p.stalled(124,{150,100},300));
    assert(!p.stalled(200,{200,100},250)); // Sustained physical progress.
    assert(!p.stalled(400,{200,100},70)); // Arrival must not trigger rerouting.
    std::cout<<"Probe ownership: pre-delegate renewal blocks historical scout cycle; real progress/stall checks passed\n";
}
