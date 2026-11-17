#pragma once
#include "CommanderRules.h"
#include <algorithm>
#include <cmath>
#include <vector>

namespace CommanderRendezvous {
using Point=CommanderRules::Point;
using CommanderRules::distance;
constexpr double arrival=192,epsilon=.0001;
struct Hazard {Point position;double radius,weight;};
inline double exposure(Point p,const std::vector<Hazard>& hazards) {
    double result=0;
    for(const auto& h:hazards)result+=h.weight*std::max(0.0,(h.radius-distance(p,h.position))/h.radius);
    return result;
}
inline double peak(Point a,Point b,const std::vector<Hazard>& hazards) {
    int steps=std::max(1,int(std::ceil(distance(a,b)/24)));double worst=0;
    for(int i=0;i<=steps;++i) {
        double t=double(i)/steps;
        worst=std::max(worst,exposure({a.x+(b.x-a.x)*t,a.y+(b.y-a.y)*t},hazards));
    }
    return worst;
}
inline bool safeLeg(Point a,Point b,const std::vector<Hazard>& hazards) {
    double current=exposure(a,hazards),end=exposure(b,hazards),worst=peak(a,b,hazards);
    return current<=epsilon?worst<=epsilon:end+epsilon<current && worst<=current+epsilon;
}
inline double progress(Point from,Point to,Point p) {
    double dx=to.x-from.x,dy=to.y-from.y;
    return ((p.x-from.x)*dx+(p.y-from.y)*dy)/std::max(1.0,std::hypot(dx,dy));
}
inline bool frontUnit(Point home,Point front,Point rally,Point unit,bool threatened) {
    return threatened || progress(home,front,unit)>=progress(home,front,rally)-96;
}
// An idle rear reinforcement may stop or detour, but must never receive the old
// generic "move back toward home" escape order when its route is safe.
inline bool rearAdvance(Point from,Point rally,Point step) {
    return progress(from,rally,step)>=-16;
}
struct Candidate {Point position;bool reachable=true,onGroundRoute=false;};
struct Choice {Point position;bool moving=false;double risk=0,routePeak=0;bool onGroundRoute=false;};
inline Choice choose(Point from,Point waypoint,Point rally,Point previous,bool hasPrevious,bool rear,
                     const std::vector<Hazard>& hazards,const std::vector<Candidate>& candidates) {
    Choice result{from,false,exposure(from,hazards),exposure(from,hazards)};double best=1e18;
    bool endangered=result.risk>epsilon;
    for(const auto& c:candidates) {
        if(!c.reachable || distance(from,c.position)<24 || !safeLeg(from,c.position,hazards))continue;
        if(rear && !endangered && !rearAdvance(from,rally,c.position) && !c.onGroundRoute)continue;
        double reverse=0;
        if(endangered && hasPrevious && distance(from,previous)>64) {
            double ax=previous.x-from.x,ay=previous.y-from.y,bx=c.position.x-from.x,by=c.position.y-from.y;
            reverse=std::max(0.0,-(ax*bx+ay*by)/std::max(1.0,std::hypot(ax,ay)*std::hypot(bx,by)))*160;
        }
        double risk=exposure(c.position,hazards);
        double cost=risk*20000+distance(c.position,waypoint)+distance(c.position,rally)*.15+reverse;
        if(cost<best){best=cost;result={c.position,true,risk,peak(from,c.position,hazards),c.onGroundRoute};}
    }
    return result;
}
}
