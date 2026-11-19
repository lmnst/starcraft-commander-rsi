#pragma once
#include "CommanderRules.h"
namespace CommanderAssembly {
constexpr double fieldRadius=256,arrival=48,step=192;
constexpr double refugeRadius=384;
inline bool boundedStep(double currentDistance,double nextDistance,bool advancing) {
    return nextDistance<=refugeRadius || (currentDistance>refugeRadius && advancing && nextDistance<currentDistance);
}
inline bool preservePost(double fromAnchor) {return fromAnchor<=fieldRadius;}
inline bool returnFire(bool worker,bool detected,bool combat,bool inRange) {
    return !worker && detected && combat && inRange;
}
inline bool localEscape(double risk,bool underAttack,bool lowHealth,bool outranged) {
    return risk>.0001 && (underAttack || lowHealth || outranged);
}
struct Progress {
    CommanderRules::Point goal{0,0};double best=1e18;int since=-1;
    bool stalled(int frame,CommanderRules::Point from,CommanderRules::Point destination) {
        double remaining=CommanderRules::distance(from,destination);
        if(since<0 || CommanderRules::distance(goal,destination)>24 || remaining+16<best || remaining<=arrival) {
            goal=destination;best=remaining;since=frame;return false;
        }
        return frame-since>=120;
    }
};
}
