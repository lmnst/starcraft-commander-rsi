#pragma once
#include "CommanderRules.h"

namespace CommanderDetection {
constexpr double contactRadius=320, evadeRadius=240, clearRadius=448;
inline bool safeCombat(CommanderRules::Point from,CommanderRules::Point to,CommanderRules::Point threat) {
    double dx=to.x-from.x,dy=to.y-from.y;
    double t=std::clamp(((threat.x-from.x)*dx+(threat.y-from.y)*dy)/std::max(1.0,dx*dx+dy*dy),0.0,1.0);
    return CommanderRules::distance({from.x+t*dx,from.y+t*dy},threat)>=evadeRadius+16;
}
inline bool observable(bool exists,bool visible,bool detected,int hitPoints) {
    return exists && visible && (hitPoints>0 || !detected);
}
inline bool protects(int key,bool withdrawing,bool flying,bool stationary=false) {
    return ((key>=5 && key<=7) || (key==8 && stationary)) && !withdrawing && !flying;
}
// Losing visibility is not evidence that a cloaked attacker has gone away.
// Only detection, death, a covered visible site, or a distant stale contact
// releases a local safety pause. The strategic task is never replaced.
inline bool release(bool alive,bool detected,bool siteChecked,int age,double distance) {
    return !alive || detected || siteChecked || (age>240 && distance>clearRadius);
}
inline bool evade(double distance,bool attackingUs) {
    return distance<evadeRadius || (attackingUs && distance<contactRadius);
}
}
