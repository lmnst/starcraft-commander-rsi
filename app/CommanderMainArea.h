#pragma once
#include "CommanderRules.h"
#include <limits>

namespace CommanderMainArea {
using Point=CommanderRules::Point;
constexpr double radius=768,coreRadius=448,frontierDepth=192;
inline bool contains(Point p,Point centre,const std::vector<Point>& otherBases) {
    double d=CommanderRules::distance(p,centre);
    if(d>radius)return false;
    if(d<=coreRadius)return true;
    for(auto b:otherBases)if(CommanderRules::distance(p,b)+96<d)return false;
    return true;
}
// Brood War's discrete psi mask, as used by BWAPI shared/Templates.h.
// Only already visible enemy pylons/buildings are supplied by the adapter.
inline bool powers(Point pylon,Point building) {
    int dx=int(building.x-pylon.x),dy=int(building.y-pylon.y);
    if(std::abs(dx)>=256 || std::abs(dy)>=160)return false;
    constexpr unsigned rows[10]={0x07e0,0x3ffc,0x7ffe,0xffff,0xffff,0xffff,0xffff,0x7ffe,0x3ffc,0x03e0};
    return (rows[(dy+160)/32]&(1u<<((dx+256)/32)))!=0;
}
struct Building {
    int id=-1;bool depot=false,pylon=false,defensive=false;int powerValue=0;
    double distance=0,health=1;
};
inline int choose(const std::vector<Building>& buildings,int current) {
    bool outer=std::any_of(buildings.begin(),buildings.end(),[](const Building& b){return !b.depot;});
    double nearest=1e18;
    for(const auto& b:buildings)if(!outer || !b.depot)nearest=std::min(nearest,b.distance);
    // Finish a reachable outer structure before walking to another one.
    // A newly discovered outer building may interrupt the core building.
    for(const auto& b:buildings)if(b.id==current && (!outer || !b.depot))return current;
    int result=-1,bestTier=99;double score=1e18;
    for(const auto& b:buildings) {
        if((outer && b.depot) || b.distance>nearest+frontierDepth)continue;
        int tier=b.defensive && b.distance<=400?0:b.pylon?1:b.depot?3:2;
        double value=b.distance+b.health*64-std::min(3,b.powerValue)*48;
        if(tier<bestTier || (tier==bestTier && value<score)){result=b.id;bestTier=tier;score=value;}
    }
    return result;
}
inline bool complete(int knownBuildings,int unchecked,bool visibleArmy,bool paused,int emptyFrames) {
    return knownBuildings==0 && unchecked==0 && !visibleArmy && !paused && emptyFrames>=96;
}
}
