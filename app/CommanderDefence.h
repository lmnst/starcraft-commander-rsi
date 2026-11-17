#pragma once
#include "CommanderRules.h"

namespace CommanderDefence {
using Point=CommanderRules::Point;
using CommanderRules::distance;
constexpr double assetRadius=640,workerRadius=480,zoneRadius=768,postRadius=512;
constexpr double enterRadius=512,assetEntry=192,leaveRadius=832;
constexpr double chaseDistance=192,targetDistance=320,postArrival=96;
constexpr int chaseFrames=144,restFrames=96;
inline bool arrived(bool active,double toBase,double toAsset,double toPost) {
    return active?toBase<=leaveRadius:toBase<=enterRadius || toAsset<=assetEntry || toPost<=160;
}
inline bool candidate(bool visible,bool detected,bool threatening,bool attackingAsset,double toBase,double toAsset,double reach) {
    return visible && detected && threatening && toBase<=zoneRadius &&
        (attackingAsset || toAsset<=std::max(224.0,std::min(384.0,reach+96)));
}
// A chase has a fixed origin. Switching enemies or moving the guard post must
// not reset that origin; all units also remain inside the locked base's zone.
struct Guard {
    bool anchored=false,returning=false;
    Point origin{0,0};
    int since=-1,until=-1,target=-1;
    void recall(int frame) { returning=true;until=frame+restFrames;target=-1; }
    bool ready(int frame,Point unit,Point post) {
        if(returning && frame>=until && distance(unit,post)<=postArrival) {
            returning=false;anchored=false;since=-1;
        }
        return !returning;
    }
    bool allow(int frame,Point unit,Point base,Point enemy,int id,bool inRange) {
        if(returning)return false;
        if(distance(unit,base)>zoneRadius || distance(enemy,base)>zoneRadius) {recall(frame);return false;}
        if(!anchored){origin=unit;anchored=true;}
        if(distance(unit,origin)>chaseDistance || distance(enemy,origin)>targetDistance ||
            (!inRange && since>=0 && frame-since>=chaseFrames)) {recall(frame);return false;}
        if(!inRange && since<0)since=frame;
        // In-range firing is stationary; keep the spatial anchor, and only
        // restart the time allowance when no longer chasing at all.
        if(inRange)since=-1;
        target=id;return true;
    }
    void noTarget(int frame,Point unit,Point post) {
        if(anchored && !returning) {
            if(distance(unit,post)>postArrival)recall(frame);
            else {anchored=false;since=-1;target=-1;}
        }
    }
};
}
