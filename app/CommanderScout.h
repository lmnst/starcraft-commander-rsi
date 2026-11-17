#pragma once
#include "CommanderRules.h"

namespace CommanderScout {
// The frozen baseline's commandReady requires 16 frames since the last order.
// Renew only the rescued Probe BEFORE invoking it, so its old scouting branch
// cannot enqueue a competing command. No other worker's cadence is changed.
constexpr int heartbeat=8,stallFrames=96;
inline bool renew(int frame,int lastOrder,int settleUntil,bool matches) {
    return !matches || frame<=settleUntil || frame-lastOrder>=heartbeat;
}
struct Progress {
    CommanderRules::Point anchor{0,0};
    int since=0;
    void reset(int frame,CommanderRules::Point position) {anchor=position;since=frame;}
    bool stalled(int frame,CommanderRules::Point position,double remaining) {
        if(CommanderRules::distance(anchor,position)>=48){reset(frame,position);return false;}
        return remaining>96 && frame-since>=stallFrames;
    }
};
}
