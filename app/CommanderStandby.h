#pragma once
#include "CommanderRules.h"

namespace CommanderStandby {
using Point=CommanderRules::Point;
using CommanderRules::distance;
constexpr double baseRadius=768,perimeter=256,spacing=80,arrival=40;
constexpr double interceptDistance=128,targetDistance=192,stationaryRange=384;
constexpr int chaseFrames=96,restFrames=72;
struct Building { Point center;double halfWidth,halfHeight; };
struct Lane { Point from,to;double width; };
struct World { std::vector<Building> buildings;std::vector<Lane> lanes; };
inline double segmentDistance(Point p,Point a,Point b) {
    double x=b.x-a.x,y=b.y-a.y,length=x*x+y*y;
    double t=length>0?std::clamp(((p.x-a.x)*x+(p.y-a.y)*y)/length,0.0,1.0):0;
    return distance(p,{a.x+t*x,a.y+t*y});
}
inline bool open(Point p,double clearance,const World& world) {
    for(const auto& b:world.buildings)
        if(std::abs(p.x-b.center.x)<b.halfWidth+clearance && std::abs(p.y-b.center.y)<b.halfHeight+clearance)return false;
    for(const auto& lane:world.lanes)if(segmentDistance(p,lane.from,lane.to)<lane.width+clearance)return false;
    return true;
}
inline Point preferred(Point origin,bool nearBase,Point base,Point facing) {
    if(!nearBase)return origin; // Field cleanup never implicitly orders a trip home.
    double length=distance(base,facing);
    if(length<1)return {base.x+384,base.y};
    return {base.x+(facing.x-base.x)*384/length,base.y+(facing.y-base.y)*384/length};
}
inline bool area(Point p,bool nearBase,Point base,Point origin) {
    return nearBase?distance(p,base)>=perimeter && distance(p,base)<=baseRadius+256:distance(p,origin)<=640;
}
inline std::vector<Point> offsets(int count) {
    int extent=std::max(2,int(std::ceil(std::sqrt(double(std::max(1,count))))));
    std::vector<Point> result;
    for(int x=-extent;x<=extent;++x)for(int y=-extent;y<=extent;++y)result.push_back({x*spacing,y*spacing});
    std::sort(result.begin(),result.end(),[](Point a,Point b) {
        double da=a.x*a.x+a.y*a.y,db=b.x*b.x+b.y*b.y;
        return da!=db?da<db:a.x!=b.x?a.x<b.x:a.y<b.y;
    });
    return result;
}
// The post is fixed for the whole interception, including target switches.
// Returning requires both time and actual arrival, so repeated threats cannot
// keep renewing a chase or progressively drag the formation farther away.
struct Guard {
    bool engaged=false,returning=false;
    int since=-1,until=-1,target=-1;
    void recall(int frame) { returning=true;engaged=false;target=-1;until=frame+restFrames; }
    void ready(int frame,Point unit,Point post) {
        if(returning && frame>=until && distance(unit,post)<=arrival) {returning=false;since=-1;}
    }
    bool permits(int frame,Point unit,Point post,Point enemy,bool inRange) const {
        return !returning && (engaged || distance(unit,post)<=arrival+24) &&
            distance(unit,post)<=interceptDistance && distance(enemy,post)<=(inRange?stationaryRange:targetDistance) &&
            (inRange || since<0 || frame-since<chaseFrames);
    }
    void attack(int frame,int id,bool inRange) {
        engaged=true;target=id;
        if(inRange)since=-1;else if(since<0)since=frame;
    }
    void noTarget(int frame,Point unit,Point post) {
        if(engaged) {
            if(distance(unit,post)>arrival)recall(frame);
            else {engaged=false;since=-1;target=-1;}
        }
    }
};
}
