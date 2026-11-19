#pragma once
#include "CommanderRules.h"
#include <map>
#include <set>
#include <vector>

namespace CommanderTargeting {
using Point=CommanderRules::Point;
constexpr double acquireRadius=400,unitLeash=256,targetLeash=400;
constexpr int chaseFrames=192,releaseFrames=96,ignoreFrames=480;
constexpr double focusRadius=224,focusApproach=224;
constexpr size_t focusSize=6;
struct Candidate {
    int id=-1;
    bool activeThreat=false,nearbyThreat=false,worker=false,building=false;
    bool valid=true,inRange=false,importantBuilding=false,objectiveBuilding=false,stationary=false,fleeing=false;
    bool armed=false,attackingSelf=false;
    double distance=0,health=1,anchorDistance=0;
};
inline int priority(const Candidate& c) {
    return c.worker?2:c.activeThreat?0:c.nearbyThreat || c.armed?1:!c.building?2:c.importantBuilding?3:4;
}
inline const char* reason(const Candidate& c) {
    return c.activeThreat?"attacking_ours":c.nearbyThreat?"nearby_threat":c.worker?"enemy_worker":!c.building?"enemy_unit":c.importantBuilding?"important_building":"ordinary_building";
}
// A combatant remains a threat between shots. Its firing animation must not
// continually pull our melee units away from another reachable combatant.
inline int combatClass(const Candidate& c) {
    return !c.worker && (c.activeThreat || c.nearbyThreat || (c.armed && (c.inRange || c.distance<=focusApproach)))?0:priority(c);
}
inline bool reachableFocus(const Candidate& c) { return c.inRange || c.distance<=focusApproach; }
inline bool directDefence(const Candidate& c) {
    return (c.activeThreat && (c.inRange || (!c.fleeing && c.distance<=192))) || (!c.worker && c.armed && c.inRange);
}
struct Choice {
    int id=-1,tier=99; double score=1e18; bool blockedThreat=false;
    const char* decision="threat_priority";
};
struct Fighter { int id; Point position; std::vector<Candidate> candidates; };
struct Focus { int target=-1,group=-1; };
// Each task owns its board. Groups have a bounded diameter (all members are
// within focusRadius of their leader) and at most six members. Candidates
// supplied here have already passed each member's pursuit/visibility limits.
class FocusBoard {
    std::map<int,Focus> previous;
public:
    std::map<int,Focus> update(std::vector<Fighter> fighters) {
        std::sort(fighters.begin(),fighters.end(),[](const Fighter& a,const Fighter& b){return a.id<b.id;});
        std::set<int> grouped;
        std::map<int,Focus> assignments;
        std::map<int,int> claimed;
        for(const auto& leader:fighters) {
            if(grouped.count(leader.id))continue;
            std::vector<const Fighter*> members{&leader};grouped.insert(leader.id);
            for(const auto& f:fighters)if(!grouped.count(f.id) && members.size()<focusSize &&
                CommanderRules::distance(leader.position,f.position)<=focusRadius) {
                members.push_back(&f);grouped.insert(f.id);
            }
            struct Support { int count=0,inRange=0,tier=99,votes=0;double distance=0,health=1; };
            std::map<int,Support> targets;
            for(auto f:members)for(const auto& c:f->candidates)if(c.valid && reachableFocus(c)) {
                auto& s=targets[c.id];++s.count;s.inRange+=c.inRange;
                s.tier=std::min(s.tier,combatClass(c));s.distance+=c.distance;s.health=std::min(s.health,c.health);
                auto old=previous.find(f->id);if(old!=previous.end() && old->second.target==c.id)++s.votes;
            }
            const int majority=int(members.size()+1)/2;
            int target=-1,tier=99;double score=1e18;
            for(const auto& entry:targets) {
                const auto& s=entry.second;if(s.count<majority)continue;
                double value=s.distance/s.count+s.health*80-s.count*72-s.inRange*32+claimed[entry.first]*48;
                if(s.tier<tier || (s.tier==tier && value<score)){target=entry.first;tier=s.tier;score=value;}
            }
            // Finish the group's live, reachable target before reacting to
            // another enemy's next shot. New higher-class threats still win.
            int votes=0;
            for(const auto& entry:targets) {
                const auto& s=entry.second;
                if(s.count>=majority && s.tier==tier && s.votes>votes){target=entry.first;votes=s.votes;}
            }
            if(target>=0)claimed[target]+=int(members.size());
            for(auto f:members)assignments[f->id]={target,leader.id};
        }
        previous=assignments;return assignments;
    }
    void clear() { previous.clear(); }
};
class Engagement {
public:
    bool anchored=false;
    Point anchor{0,0},releaseOrigin{0,0};
    int target=-1,chaseSince=-1,releaseUntil=-1;
    bool releasing=false,advanceOnly=false;
    std::map<int,int> ignored;
    void release(int frame,Point position) {
        if(target>=0)ignored[target]=frame+ignoreFrames;
        target=-1;chaseSince=-1;releaseUntil=frame+releaseFrames;
        releaseOrigin=position;releasing=true;anchored=false;
    }
    void resume(int frame,Point position,double goalDistance) {
        if(releasing && frame>=releaseUntil && (CommanderRules::distance(position,releaseOrigin)>=128 || goalDistance<=112))releasing=false;
    }
    bool needsRelease(int frame,Point position,const Candidate& previous) const {
        return anchored && !releasing && previous.id==target && !previous.building && !previous.stationary && !directDefence(previous) &&
            (!previous.valid || previous.distance>acquireRadius || previous.anchorDistance>targetLeash ||
             CommanderRules::distance(position,anchor)>unitLeash || (chaseSince>=0 && frame-chaseSince>=chaseFrames));
    }
    bool blockAutoAcquire(int frame,const std::vector<Candidate>& candidates) const {
        if(releasing)return true;
        for(const auto& c:candidates) {
            auto found=ignored.find(c.id);
            if(c.distance<=acquireRadius && found!=ignored.end() && frame<found->second)return true;
        }
        return false;
    }
    Choice choose(int frame,Point position,const std::vector<Candidate>& candidates,int focus=-1) const {
        Choice best;
        std::vector<const Candidate*> allowed;
        for(const auto& c:candidates) {
            if(!c.valid || c.distance>acquireRadius)continue;
            bool directDefense=directDefence(c);
            auto ignore=ignored.find(c.id);
            bool blocked=(ignore!=ignored.end() && frame<ignore->second && !directDefense) ||
                (releasing && !c.activeThreat && !directDefense) || (!directDefense && !c.building && !c.stationary && !c.inRange &&
                ((anchored && (CommanderRules::distance(position,anchor)>unitLeash || c.anchorDistance>targetLeash)) ||
                 (chaseSince>=0 && frame-chaseSince>=chaseFrames)));
            if(blocked){if(c.activeThreat)best.blockedThreat=true;continue;}
            allowed.push_back(&c);
            int tier=priority(c);
            double score=c.distance+(c.inRange?0:64)+c.health*12-(c.id==target?24:0)-(c.objectiveBuilding?96:0);
            if(tier<best.tier || (tier==best.tier && score<best.score)){best.id=c.id;best.tier=tier;best.score=score;}
        }
        const Candidate *current=nullptr,*shared=nullptr,*fresh=nullptr;
        for(auto c:allowed) {
            if(c->id==target)current=c;
            if(c->id==focus && reachableFocus(*c))shared=c;
            if(c->id==best.id)fresh=c;
        }
        const Candidate* selected=fresh;
        if(current && reachableFocus(*current) && !releasing && fresh && combatClass(*current)<=combatClass(*fresh)) {
            selected=current;best.decision="finish_target";
        }
        if(shared && fresh && combatClass(*shared)<=combatClass(*fresh) &&
            (!selected || !selected->inRange || shared->inRange || combatClass(*shared)<combatClass(*selected))) {
            selected=shared;best.decision="local_focus";
        }
        // A close attacker threatening this particular unit can interrupt a
        // distant chase. Do not abandon an enemy already in weapon range.
        if(!selected || (!selected->inRange && selected->distance>128))for(auto c:allowed)
            if(c->attackingSelf && !c->worker && !c->fleeing && (c->inRange || c->distance<=96) &&
                (!selected || !selected->attackingSelf || c->distance<selected->distance)) {
                selected=c;best.decision="urgent_self_defence";
            }
        if(selected){best.id=selected->id;best.tier=priority(*selected);}
        if(best.blockedThreat && best.tier>=2 && (!selected || combatClass(*selected)>0))best.id=-1;
        return best;
    }
    void commit(int frame,Point position,int id,bool inRange,bool mobile=true) {
        if(!mobile){anchored=false;releasing=false;target=id;chaseSince=-1;return;}
        auto oldIgnore=ignored.find(id);
        if(releasing && (oldIgnore==ignored.end() || frame>=oldIgnore->second)){releasing=false;anchored=false;}
        if(!anchored && !releasing){anchor=position;anchored=true;}
        target=id;
        if(inRange)chaseSince=-1;
        else if(chaseSince<0)chaseSince=frame;
    }
};
}
