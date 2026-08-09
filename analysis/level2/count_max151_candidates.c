#include <stdio.h>
#include <stdlib.h>

#define MAX151_ADAPTIVE7
#include "../../solution/max151_adaptive8.c"

i64 get_rand(int salt) { (void)salt; return 0; }
int host_run(int level, const char *rle, int length, int generations, char *out) {
  (void)level; (void)rle; (void)length; (void)generations; (void)out; return -1;
}
int host_submit(int level, i64 value, i64 known, i64 guess) {
  (void)level; (void)value; (void)known; (void)guess; return 0;
}
int host_finalize(void) { return 0; }

static int candidate_matches_schedule(
    u64 candidate, const int starts[7], const u16 observed[7]) {
  u8 trits[64]; sf2_fill_trits(candidate,trits);
  for(int probe=0;probe<7;probe++) {
    u32 rank=sf2_context_rank(trits,starts[probe]);
    if(rank>=47321 || SF2CTXCLASS[rank]!=observed[probe]) return 0;
  }
  return 1;
}

int main(int argc, char **argv) {
  if(argc<2) { fprintf(stderr,"usage: %s SECRET_HEX...\n",argv[0]); return 2; }
  sf2_init_classes();
  sf2_init_forced_literals();
  for(int argument=1;argument<argc;argument++) {
  u64 secret=(u64)strtoull(argv[argument],0,0);
  u8 trits[64]; sf2_fill_trits(secret,trits);
  const int baseline[8]={0,6,18,30,36,42,48,54};
  for(int omitted=0;omitted<8;omitted++) {
  int starts[7]; u16 observed[7]; int count=0;
  u64 known=0,value=0;
  for(int source=0;source<8;source++) {
    if(source==omitted) continue;
    int start=baseline[source];
    starts[count]=start;
    u32 rank=sf2_context_rank(trits,start);
    int lookup=(int)SF2CTXCLASS[rank];
    observed[count++]=(u16)lookup;
    u16 local_known=SF2FORCEDKNOWN[lookup-1];
    u16 local_value=SF2FORCEDVALUE[lookup-1];
    for(int position=0;position<12;position++) {
      int bit=start-3+position;
      if(bit>=0 && bit<64 && (local_known&(1u<<position))) {
        known|=1ULL<<bit;
        if(local_value&(1u<<position)) value|=1ULL<<bit;
      }
    }
  }
  u64 unknown=~known,subset=unknown;
  int unknown_count=sf2_popcount64(unknown);
  if(unknown_count>18) {
    printf("secret=0x%016llx omit=%d start=%d unknown=%d candidates=skipped\n",
           secret,omitted,baseline[omitted],unknown_count);
    continue;
  }
  u32 survivors=0;
  do {
    u64 candidate=(value&known)|subset;
    if(candidate_matches_schedule(candidate,starts,observed)) survivors++;
    if(!subset) break;
    subset=(subset-1)&unknown;
  } while(1);
  printf("secret=0x%016llx omit=%d start=%d unknown=%d candidates=%u\n",
         secret,omitted,baseline[omitted],unknown_count,survivors);
  }
  }
  return 0;
}
