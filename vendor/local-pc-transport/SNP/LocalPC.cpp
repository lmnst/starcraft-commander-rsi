
#include "LocalPC.h"

#include "Output.h"
#include "LocalQueue.h"
#include <cstdlib>
#include <cstdio>
#include <string>

#include <Util/SharedMemory.h>
#include <Util/SharedStructure.h>
#include <Util/Mutex.h>

namespace SMEM
{
  SNP::NetworkInfo networkInfo = {"Local PC", 'SMEM', "",
    // CAPS:
  {sizeof(CAPS), 0x20000003, SNP::PACKET_SIZE, 16, 256, 1000, 50, 8, 2}};

#define INTERLOCKED Util::Mutex::Lock globalLock(mutex);

  struct Packet
  {
    int sender;
    int length;
    char data[508];
  };
  struct PeerData
  {
    unsigned lastOccupied; // timer
    char ad[512];
    bool isAdvertising;
    int incomingCount;
    Packet incoming[LocalQueue::capacity];

    bool inline isOccupied() const
    {
      return lastOccupied > 0; // && GetTickCount() < lastOccupied + 3000;
    }
  };
  struct SharedData
  {
    PeerData peer[8];
  };
  static std::string scopedName(const char* suffix) {
    std::string name="Local\\LocalPC_Network_";
    const char* scope=std::getenv("COMMANDER_NETWORK_SCOPE");
    if(scope && *scope) {
      for(int i=0;scope[i] && i<48;++i)
        if((scope[i]>='a' && scope[i]<='z') || (scope[i]>='A' && scope[i]<='Z') || (scope[i]>='0' && scope[i]<='9'))name+=scope[i];
      name+="_";
    }
    return name+suffix;
  }
  const std::string memoryName=scopedName("Memory");
  const std::string mutexName=scopedName("Mutex");
  Util::SharedStructure<SharedData> session;
  Util::Mutex mutex(mutexName.c_str());

  SharedData *shd = NULL;

  int self;
  static unsigned sentCount=0,receivedCount=0,fullCount=0,lastTrace=0,peakQueue=0;
  static void traceTransport(const char* reason,bool force=false) {
    if(!std::getenv("COMMANDER_DIAGNOSTICS") && !std::getenv("COMMANDER_NETWORK_SCOPE"))return;
    unsigned now=GetTickCount();if(!force && now-lastTrace<1000)return;lastTrace=now;
    FILE* file=nullptr;fopen_s(&file,"bwapi-data/write/transport-diagnostic.jsonl","a");if(!file)return;
    std::fprintf(file,"{\"tick_ms\":%u,\"pid\":%lu,\"self\":%d,\"event\":\"%s\",\"sent\":%u,\"received\":%u,\"full\":%u,\"peak_queue\":%u,\"peers\":[",
        now,GetCurrentProcessId(),self,reason,sentCount,receivedCount,fullCount,peakQueue);
    for(int i=0;i<8;++i)std::fprintf(file,"%s{\"id\":%d,\"occupied\":%s,\"age_ms\":%u,\"queued\":%d}",i?",":"",i,
        shd->peer[i].isOccupied()?"true":"false",shd->peer[i].lastOccupied?now-shd->peer[i].lastOccupied:0,shd->peer[i].incomingCount);
    std::fprintf(file,"]}\n");std::fclose(file);
  }

  //------------------------------------------------------------------------------------------------------------------------------------
  void LocalPC::processIncomingPackets()
  {
    INTERLOCKED;
    try
    {
      // mark our slot
      shd->peer[self].lastOccupied = GetTickCount();

      // pass all packets to storm
      /*int incomingCount = shd->peer[self].incomingCount;
      if(incomingCount > 16)
        incomingCount = 16;*/
      const int incomingCount = LocalQueue::readable(shd->peer[self].incomingCount);
      receivedCount+=incomingCount;peakQueue=std::max(peakQueue,unsigned(incomingCount));
      for(int i = 0; i < incomingCount; i++)
      {
        Packet &packet = shd->peer[self].incoming[i];
        passPacket(packet.sender, Util::MemoryFrame(packet.data, packet.length));
      }

      // clear incoming queue
      shd->peer[self].incomingCount = 0;
      traceTransport("receive");
    }
    catch(GeneralException &e)
    {
      DropLastError("processIncomingPackets failed: %s", e.getMessage());
    }
  }
  //------------------------------------------------------------------------------------------------------------------------------------
  //------------------------------------------------------------------------------------------------------------------------------------
  void LocalPC::initialize()
  {
    INTERLOCKED;

    // init shared stuff
    bool just_created = session.create(memoryName.c_str());
    shd = &session.get();

    // if the shared memory was just created, init it
    if(just_created)
    {
      DropMessage(0, "Memory newly created");
      memset(shd, 0, sizeof(SharedData));
    }

    // reserve an index for oneself
    self = -1;
    for(int i = 0; i < 8; i++)
    {
      if(!shd->peer[i].isOccupied())
      {
        self = i;
        break;
      }
    }
    DropMessage(0, "self: %d", self);
    if(self == -1)
    {
      DropMessage(2, "could not reserve self. exiting");
      exit(1);
    }
    PeerData &selfData = shd->peer[self];
    selfData.lastOccupied = GetTickCount();
    selfData.isAdvertising = false;
    selfData.incomingCount = 0;
    traceTransport("initialize",true);

  }
  void LocalPC::destroy()
  {
    if (shd)
      shd->peer[self].lastOccupied = 0;
    session.release();
  }
  void LocalPC::requestAds()
  {
    processIncomingPackets();

    INTERLOCKED;
    for(int i = 0; i < 8; i++)
    {
      if(shd->peer[i].isOccupied())
      {
        if(shd->peer[i].isAdvertising)
        {
          passAdvertisement(i, Util::MemoryFrame::from(shd->peer[i].ad));
        }
      }
    }
  }
  void LocalPC::sendAsyn(const int& him, Util::MemoryFrame packet)
  {
    processIncomingPackets();

    if(!shd->peer[him].isOccupied())
      return;

    INTERLOCKED;
    // push the packet on target's packetqueue
    PeerData &peerData = shd->peer[him];
    int slotIndex = LocalQueue::reserve(peerData.incomingCount);
    if(slotIndex < 0)
    {
      ++fullCount;traceTransport("queue_full",true);
      // Do not claim a successful asynchronous send after losing its packet.
      // spiSend converts this to FALSE so Storm can retry/back off.
      throw GeneralException("Local PC receive queue is full; send not accepted");
    }
    if(packet.size() > 508)
    {
      DropMessage(1, "packet bigger than 508 bytes (%d), being cut", packet.size());
    }
    Util::MemoryFrame::from(peerData.incoming[slotIndex].data).write(packet);
    peerData.incoming[slotIndex].length = packet.size();
    peerData.incoming[slotIndex].sender = self;
    ++sentCount;
  }
  void LocalPC::receive()
  {
    processIncomingPackets();
  }
  void LocalPC::startAdvertising(Util::MemoryFrame ad)
  {
    INTERLOCKED;
    Util::MemoryFrame::from(shd->peer[self].ad).write(ad);
    shd->peer[self].isAdvertising = true;
  }
  void LocalPC::stopAdvertising()
  {
    shd->peer[self].isAdvertising = false;
  }
  //------------------------------------------------------------------------------------------------------------------------------------
};
