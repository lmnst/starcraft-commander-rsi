#include "../app/CommanderTargeting.h"
#include <cassert>
#include <iostream>
using namespace CommanderTargeting;

Candidate enemy(int id,double distance,bool firing=false) {
    Candidate c;c.id=id;c.distance=distance;c.armed=true;c.nearbyThreat=true;c.activeThreat=firing;return c;
}
int main() {
    // Regression: 20:09 match, frame 6214. The old rule abandoned a Dragoon
    // already in melee range for another one 218 pixels away, then came back.
    Engagement melee;melee.commit(6201,{0,0},194,true);
    auto a=enemy(194,15),b=enemy(202,218,true);a.inRange=true;
    assert(melee.choose(6214,{0,0},{a,b},194).id==194);
    // Alternating firing states must not cause a three-unit group to ping-pong.
    FocusBoard board;std::map<int,Engagement> engagements;
    int common=-1,switches=0;
    for(int frame=0;frame<240;++frame) {
        std::vector<Fighter> fighters;
        for(int id=1;id<=3;++id) {
            a=enemy(194,frame>=24?10+id:110+id*3,(frame/11)%2==0);
            a.inRange=frame>=24;
            b=enemy(202,130-id*3,(frame/11)%2!=0);
            fighters.push_back({id,{double(id*16),0},{a,b}});
        }
        auto focused=board.update(fighters);int next=-1;
        for(const auto& f:fighters) {
            auto& e=engagements[f.id];auto c=e.choose(frame,f.position,f.candidates,focused.at(f.id).target);
            assert(c.id==194 || c.id==202);
            if(next<0)next=c.id;else assert(next==c.id);
            const auto& chosen=f.candidates[c.id==194?0:1];
            e.commit(frame,f.position,c.id,chosen.inRange);
        }
        if(common>=0 && common!=next)++switches;
        common=next;
    }
    assert(switches==0);
    // A dead/unseen/unattackable target is removed from candidates immediately.
    int survivor=common==194?202:194;
    auto living=enemy(survivor,110);
    auto focused=board.update({{1,{0,0},{living}},{2,{24,0},{living}},{3,{48,0},{living}}});
    assert(focused.at(1).target==survivor);
    assert(engagements[1].choose(241,{0,0},{living},survivor).id==survivor);
    living.valid=false;
    assert(engagements[1].choose(242,{0,0},{living},survivor).id==-1);
    // Large armies form bounded groups; remote battles and incompatible
    // weapon targets never inherit another group's target.
    FocusBoard distant;
    auto ground=enemy(10,100),air=enemy(20,100);
    focused=distant.update({{1,{0,0},{ground}},{2,{40,0},{ground}},
        {3,{1000,0},{air}},{4,{1040,0},{air}}});
    assert(focused.at(1).target==10 && focused.at(3).target==20);
    assert(focused.at(1).group!=focused.at(3).group);
    std::vector<Fighter> crowd;
    for(int id=1;id<=18;++id)crowd.push_back({id,{double(id),0},{ground,air}});
    auto large=FocusBoard{}.update(crowd);std::map<int,int> groups;std::set<int> targets;
    for(auto f:large){++groups[f.second.group];targets.insert(f.second.target);}
    assert(groups.size()==3 && targets.size()==2);
    for(auto g:groups)assert(g.second<=int(focusSize));
    Engagement incompatible;air.valid=false;
    assert(incompatible.choose(0,{0,0},{ground,air},20).id==10);
    // An enemy in range is not dropped to walk toward a teammate's target.
    Engagement alreadyFighting;alreadyFighting.commit(0,{0,0},10,true);
    ground.inRange=true;air.valid=true;air.distance=180;
    assert(alreadyFighting.choose(1,{0,0},{ground,air},20).id==10);
    // Emergency self defence may interrupt a distant approach, never an
    // already achievable hit simply because another enemy fired this frame.
    Engagement threatened;threatened.commit(0,{0,0},10,false);
    ground.inRange=false;ground.distance=200;air.distance=60;air.attackingSelf=true;air.activeThreat=true;
    auto urgent=threatened.choose(1,{0,0},{ground,air},10);
    assert(urgent.id==20 && std::string(urgent.decision)=="urgent_self_defence");
    ground.inRange=true;
    assert(threatened.choose(2,{0,0},{ground,air},10).id==10);
    // Shared focus must never punch through an individual pursuit limit,
    // release/ignore timer or force a long chase for a remote group target.
    Engagement leash;leash.commit(0,{0,0},10,false);
    ground.inRange=false;ground.distance=100;ground.anchorDistance=401;
    assert(leash.needsRelease(1,{0,0},ground));
    assert(leash.choose(1,{0,0},{ground},10).id==-1);
    ground.anchorDistance=100;
    assert(leash.choose(chaseFrames,{0,0},{ground},10).id==-1);
    leash.release(chaseFrames,{0,0});
    assert(leash.choose(chaseFrames+1,{0,0},{ground},10).id==-1);
    FocusBoard remote;ground.distance=300;
    assert(remote.update({{1,{0,0},{ground}}}).at(1).target==-1);
    // Urgent military threats still interrupt building demolition; passive
    // workers/important production buildings retain their existing ordering.
    Engagement demolish;
    Candidate building;building.id=30;building.building=true;building.distance=10;building.inRange=true;
    demolish.commit(0,{0,0},30,true,false);
    auto cannon=enemy(40,120,true);cannon.building=true;cannon.stationary=true;
    assert(demolish.choose(1,{0,0},{building,cannon},30).id==40);
    board.clear();
    std::cout<<"Local focus: alternating shots, recorded melee regression, death/visibility, emergency defence, groups, weapons and pursuit bounds passed\n";
}
