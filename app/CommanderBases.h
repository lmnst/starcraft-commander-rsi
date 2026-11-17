#pragma once
#include "CommanderRules.h"
#include <map>
#include <set>

namespace CommanderBases {
using Point=CommanderRules::Point;
struct Base { int id;Point position;bool primary=false;double pressure=0;bool airThreat=false; };
struct Soldier { int id;Point position;double power=1,health=1;bool antiAir=false;std::set<int> reachable; };
inline std::map<int,int> quotas(int count,const std::vector<Base>& bases,double averagePower) {
    std::map<int,int> result;if(bases.empty())return result;
    struct Share { int id,minimum;double weight,remainder=0; };
    std::vector<Share> shares;int available=count;double total=0;
    for(const auto& b:bases) {
        double normal=bases.size()==1?count:count*(b.primary?.7:.3/(bases.size()-1));
        double extra=b.pressure>0?std::ceil(b.pressure*1.25/std::max(.25,averagePower)):0;
        int minimum=count>=int(bases.size())?(b.primary?std::min(count-int(bases.size())+1,std::max(1,int(std::ceil(count*.2)))):1):0;
        double weight=std::max(0.0,normal+extra-minimum);
        shares.push_back({b.id,minimum,weight});result[b.id]=minimum;available-=minimum;total+=weight;
    }
    for(auto& s:shares) {
        double exact=available*(total>0?s.weight/total:1.0/shares.size());
        int whole=int(std::floor(exact));result[s.id]+=whole;s.remainder=exact-whole;
    }
    int remaining=count;for(const auto& r:result)remaining-=r.second;
    std::stable_sort(shares.begin(),shares.end(),[](const Share& a,const Share& b){return a.remainder>b.remainder;});
    for(int i=0;i<remaining;++i)++result[shares[size_t(i)%shares.size()].id];
    return result;
}
class Defence {
public:
    std::map<int,int> assigned,wanted;
    std::map<int,double> retainedPressure;
    bool reinforced=false;
    void update(std::vector<Base> bases,const std::vector<Soldier>& soldiers) {
        bool currentThreat=std::any_of(bases.begin(),bases.end(),[](const Base& b){return b.pressure>0;});
        if(currentThreat) {retainedPressure.clear();for(const auto& b:bases)retainedPressure[b.id]=b.pressure;reinforced=true;}
        for(auto& b:bases)if(retainedPressure.count(b.id))b.pressure=retainedPressure.at(b.id);
        double power=0;for(const auto& s:soldiers)power+=s.power;
        wanted=quotas(int(soldiers.size()),bases,soldiers.empty()?1:power/soldiers.size());
        std::map<int,const Base*> sites;for(const auto& b:bases)sites[b.id]=&b;
        std::map<int,const Soldier*> alive;for(const auto& s:soldiers)alive[s.id]=&s;
        std::map<int,int> counts;
        for(auto it=assigned.begin();it!=assigned.end();) {
            if(!alive.count(it->first) || !sites.count(it->second) || !alive.at(it->first)->reachable.count(it->second))it=assigned.erase(it);
            else {++counts[it->second];++it;}
        }
        // Keep valid posts; release only excess members, preferably those
        // farthest away. Quota changes cannot shuffle an entire defence line.
        for(const auto& b:bases)while(counts[b.id]>wanted[b.id]) {
            int release=-1;double farthest=-1;
            for(const auto& a:assigned)if(a.second==b.id) {
                double d=CommanderRules::distance(alive.at(a.first)->position,b.position);
                if(d>farthest){release=a.first;farthest=d;}
            }
            if(release<0)break;assigned.erase(release);--counts[b.id];
        }
        std::stable_sort(bases.begin(),bases.end(),[](const Base& a,const Base& b){return a.pressure>b.pressure;});
        for(const auto& b:bases)while(counts[b.id]<wanted[b.id]) {
            int best=-1;double cost=1e18;
            for(const auto& s:soldiers)if(!assigned.count(s.id) && s.reachable.count(b.id)) {
                double value=CommanderRules::distance(s.position,b.position)+(1-s.health)*192;
                if(b.airThreat && !s.antiAir)value+=4096;
                if(value<cost){cost=value;best=s.id;}
            }
            if(best<0)break;assigned[best]=b.id;++counts[b.id];
        }
        // If a quota is unreachable, use another reachable base rather than
        // leaving a unit executing its old attack. Fully trapped units are
        // held by the adapter until a valid route appears.
        for(const auto& s:soldiers)if(!assigned.count(s.id)) {
            int best=-1;double d=1e18;
            for(const auto& b:bases)if(s.reachable.count(b.id)) {
                double value=CommanderRules::distance(s.position,b.position);
                if(value<d){d=value;best=b.id;}
            }
            if(best>=0)assigned[s.id]=best;
        }
        // A previously assigned melee garrison cannot answer an air raid.
        // Swap compatible defenders while retaining the per-base headcounts.
        // This also works when every existing unit already has a base job.
        std::vector<Base> airSites;int antiAir=0;
        for(const auto& b:bases)if(b.airThreat)airSites.push_back(b);
        for(const auto& s:soldiers)antiAir+=s.antiAir;
        auto airWanted=quotas(antiAir,airSites,1);
        std::map<int,int> airCounts;
        for(const auto& s:soldiers)if(s.antiAir && assigned.count(s.id))++airCounts[assigned.at(s.id)];
        for(const auto& b:airSites)while(airCounts[b.id]<std::min(wanted[b.id],airWanted[b.id])) {
            int donor=-1,recipient=-1,from=-1;double best=1e18;
            for(const auto& s:soldiers)if(s.antiAir && assigned.count(s.id) && assigned.at(s.id)!=b.id && s.reachable.count(b.id)) {
                int other=assigned.at(s.id);
                if(airWanted.count(other) && airCounts[other]<=std::min(wanted[other],airWanted[other]))continue;
                const auto& source=*std::find_if(bases.begin(),bases.end(),[&](const Base& x){return x.id==other;});
                for(const auto& replacement:soldiers)if(!replacement.antiAir && assigned.count(replacement.id) &&
                    assigned.at(replacement.id)==b.id && replacement.reachable.count(other)) {
                    double cost=CommanderRules::distance(s.position,b.position)+CommanderRules::distance(replacement.position,source.position);
                    if(cost<best){best=cost;donor=s.id;recipient=replacement.id;from=other;}
                }
            }
            if(donor<0)break;
            assigned[donor]=b.id;assigned[recipient]=from;++airCounts[b.id];--airCounts[from];
        }
    }
};
}
