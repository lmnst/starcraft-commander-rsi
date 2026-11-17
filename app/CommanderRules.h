#pragma once
#include <algorithm>
#include <cmath>
#include <vector>

namespace CommanderRules {
struct Point { double x, y; };
inline double distance(Point a, Point b) { return std::hypot(a.x-b.x, a.y-b.y); }
// Scout rescue never recruits a worker. Recognize an existing enemy-bound Probe
// by its move order, or the adapter's already assigned scout ID.
inline bool scoutProbe(bool probe, bool complete, bool busy, bool carrying,
                       bool assigned, bool moving, double targetToEnemy) {
    // A baseline scout can still carry minerals from its old mining job. Its
    // explicit enemy-bound move identifies scouting regardless of that cargo.
    return probe && complete && !busy && ((assigned && !carrying) || (moving && targetToEnemy < 512));
}
inline bool rescueHome(double health, int closeThreats, bool baseThreatened) {
    return health < .65 || closeThreats > 1 || baseThreatened;
}
inline bool rescueMoveNeeded(bool moving, double targetError, bool idle) {
    return !moving || targetError > 24 || idle;
}
struct Threat { Point point; double reach, strength; bool worker, active, detected; };
inline bool combatThreat(bool worker, bool armed) { return armed && !worker; }
inline bool retreatComplete(int alive,int arrived) { return !alive || arrived>=std::max(1,int(std::ceil(alive*.8))); }
enum class MainAction { Delegate, AttackCombat, Move, AttackMove };
inline MainAction mainAction(bool contact, bool workerTarget, bool workerNearby,
                             bool anchored, bool waitingForExpansion) {
    if (contact) return workerTarget ? MainAction::AttackCombat : MainAction::Delegate;
    return workerTarget || workerNearby || anchored || waitingForExpansion ? MainAction::Move : MainAction::AttackMove;
}
inline bool insideDefense(Point unit, Point enemy, Point anchor, double radius) {
    return distance(unit, anchor) <= radius && distance(enemy, anchor) <= radius;
}
inline double danger(Point point, const std::vector<Threat>& threats) {
    double result = 0;
    for (const auto& t : threats) {
        if (t.worker && !t.active) continue;
        double reach = std::max(224.0, t.reach + 128.0);
        result += t.strength * std::max(0.0, (reach-distance(point,t.point))/reach)
                  * (t.detected ? 1.0 : 3.0);
    }
    return result;
}
inline double retreatCost(Point candidate, Point origin, Point preferred,
                          const std::vector<Threat>& threats) {
    return danger(candidate, threats)*2000 + distance(candidate,preferred)
           + std::abs(distance(candidate,origin)-384)*.15;
}
}
