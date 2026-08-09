#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX151_ADAPTIVE7
#define MAX151_UNIFORM7
#include "../../solution/max151_adaptive8.c"

i64 get_rand(int salt) { (void)salt; return 0; }
int host_run(int level, const char *rle, int length, int generations, char *out) {
  (void)level; (void)rle; (void)length; (void)generations; (void)out; return -1;
}
int host_submit(int level, i64 value, i64 known, i64 guess) {
  (void)level; (void)value; (void)known; (void)guess; return 0;
}
int host_finalize(void) { return 0; }

int main(int argc, char **argv) {
  int custom_starts[SF2_JOIN_PROBES];
  int first_secret=1;
  if(argc>=4 && !strcmp(argv[1],"--starts")) {
    if(sscanf(argv[2],"%d,%d,%d,%d,%d,%d,%d",
              &custom_starts[0],&custom_starts[1],&custom_starts[2],
              &custom_starts[3],&custom_starts[4],&custom_starts[5],
              &custom_starts[6])!=SF2_JOIN_PROBES) {
      fprintf(stderr,"invalid start list\n");
      return 2;
    }
    first_secret=3;
  }
  if(argc<=first_secret) {
    fprintf(stderr,"usage: %s [--starts A,B,C,D,E,F,G] SECRET_HEX...\n",argv[0]);
    return 2;
  }
  sf2_init_classes();
  sf2_init_forced_literals();
  sf2_init_central8();
  for(int argument=first_secret;argument<argc;argument++) {
    u64 secret=(u64)strtoull(argv[argument],0,0);
    u16 observed[SF2_ADAPTIVE_RUN_CAP]={0};
    int starts[SF2_ADAPTIVE_RUN_CAP]={0};
    u64 known=0,value=0,conflict=0;
    int runs=SF2_JOIN_PROBES;
    for(int probe=0;probe<SF2_JOIN_PROBES;probe++) {
      starts[probe]=first_secret==3 ? custom_starts[probe] : SF2START[probe];
      observed[probe]=sf2_candidate_label(secret,starts[probe]);
      sf2_merge_central8_literals(
          starts[probe],observed[probe],&known,&value,&conflict);
    }
    while(sf2_popcount64(~known)>SF2_ENUM_UNKNOWN_CAP &&
          runs<SF2_ADAPTIVE_RUN_CAP) {
      int start=sf2_choose_coverage_start(known,starts,runs);
      if(start<1) break;
      starts[runs]=start;
      observed[runs]=sf2_candidate_label(secret,start);
      sf2_merge_central8_literals(
          start,observed[runs],&known,&value,&conflict);
      runs++;
    }
    int count=sf2_enumerate_central8(known,value,observed,starts,runs);
    int found=0;
    if(count>0)
      for(int index=0;index<count;index++)
        if(SF2SURVIVOR[index]==secret) found=1;
    int adaptive=-100;
    while(count>1 && runs<SF2_ADAPTIVE_RUN_CAP) {
      adaptive=sf2_choose_adaptive_start(starts,runs,count);
      if(adaptive<1) break;
      starts[runs]=adaptive;
      observed[runs]=sf2_candidate_label(secret,adaptive);
      runs++;
      count=sf2_filter_survivors(
          count,adaptive,observed[runs-1]);
    }
    found=0;
    if(count>0)
      for(int index=0;index<count;index++)
        if(SF2SURVIVOR[index]==secret) found=1;
    printf("secret=0x%016llx count=%d overflow=%d found=%d adaptive=%d runs=%d\n",
           secret,count,SF2SURVIVOROVERFLOW,found,adaptive,runs);
    printf(" labels=");
    for(int probe=0;probe<SF2_JOIN_PROBES;probe++)
      printf("%s%u",probe?",":"",(unsigned)observed[probe]);
    printf("\n");
  }
  return 0;
}
