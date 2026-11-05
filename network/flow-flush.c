/* SPDX-License-Identifier: MIT
 * Small conntrack netlink client: no target library/package replacement.
 * IPv4 deletion is restricted to an owned mwan3 member mark; IPv6 deletion
 * resets IPv6 sessions when the selected IPv6 egress/address changes.
 */
#include <arpa/inet.h>
#include <errno.h>
#include <linux/netlink.h>
#include <linux/netfilter/nfnetlink.h>
#include <linux/netfilter/nfnetlink_conntrack.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#include <signal.h>
#include <sys/wait.h>

/* A timeout applet is absent from the original BusyBox. Keep its replacement
 * local to this static helper; forward cancellation and bound child groups. */
static volatile sig_atomic_t limited_pid, timed_out, cancelled;
static void limit_signal(int sig) {
    if (sig==SIGALRM) {
        if (timed_out) { if (limited_pid>0) kill(-limited_pid,SIGKILL); return; }
        timed_out=1;
    } else cancelled=sig;
    if (limited_pid>0) kill(-limited_pid,SIGTERM);
    alarm(3);
}
static int limited_run(int argc,char **argv) {
    char *end; unsigned long seconds;
    if(argc<3)return 2;
    seconds=strtoul(argv[1],&end,10);
    if(!*argv[1]||*end||seconds<1||seconds>300)return 2;
    struct sigaction sa={0};sa.sa_handler=limit_signal;sigemptyset(&sa.sa_mask);
    sigaction(SIGALRM,&sa,NULL);sigaction(SIGTERM,&sa,NULL);sigaction(SIGINT,&sa,NULL);
    pid_t child=fork();
    if(child<0)return 125;
    if(!child){setpgid(0,0);signal(SIGALRM,SIG_DFL);signal(SIGTERM,SIG_DFL);signal(SIGINT,SIG_DFL);
        execvp(argv[2],argv+2);_exit(errno==ENOENT?127:126);}
    limited_pid=child;setpgid(child,child);alarm((unsigned)seconds);
    int status;
    while(waitpid(child,&status,0)<0){if(errno!=EINTR)return 125;}
    alarm(0);limited_pid=0;
    if(timed_out)return 124;
    if(cancelled)return 128+cancelled;
    return WIFEXITED(status)?WEXITSTATUS(status):128+WTERMSIG(status);
}


struct request { struct nlmsghdr header; struct nfgenmsg gen; unsigned char attrs[32]; };
static void attr(struct request *r, unsigned short type, uint32_t value) {
    struct nlattr *a = (void *)((char *)r + NLMSG_ALIGN(r->header.nlmsg_len));
    a->nla_type = type; a->nla_len = sizeof(*a) + sizeof(value);
    value = htonl(value); memcpy((char *)a + sizeof(*a), &value, sizeof(value));
    r->header.nlmsg_len = NLMSG_ALIGN(r->header.nlmsg_len) + NLA_ALIGN(a->nla_len);
}
static void make_request(struct request *r, int family, uint32_t mark) {
    memset(r, 0, sizeof(*r));
    r->header.nlmsg_len = NLMSG_LENGTH(sizeof(r->gen));
    r->header.nlmsg_type = (NFNL_SUBSYS_CTNETLINK << 8) | IPCTNL_MSG_CT_DELETE;
    r->header.nlmsg_flags = NLM_F_REQUEST | NLM_F_ACK;
    r->header.nlmsg_seq = 1;
    /* Linux 5.4 treats version zero as AF_UNSPEC for a tuple-less DELETE.
     * Version one is required to honor nfgen_family for filtered flushes. */
    r->gen.nfgen_family = family; r->gen.version = NFNETLINK_V0 + 1;
    if (family == AF_INET) { attr(r, CTA_MARK, mark); attr(r, CTA_MARK_MASK, 0x00ff0000); }
}

struct entry {struct entry *next;unsigned length;unsigned char attrs[1200];};
static const struct nlattr *find_attr(const void *data,int size,unsigned type) {
    const struct nlattr *a=data;
    while(size>=(int)sizeof(*a) && a->nla_len>=sizeof(*a) && a->nla_len<=size) {
        if((a->nla_type&NLA_TYPE_MASK)==type)return a;
        int step=NLA_ALIGN(a->nla_len);size-=step;a=(const void *)((const char *)a+step);
    }
    return NULL;
}
static const struct nlattr *nested(const struct nlattr *a,unsigned type) {
    return a?find_attr((const char *)a+NLA_HDRLEN,a->nla_len-NLA_HDRLEN,type):NULL;
}
static int prefix_parse(const char *text,unsigned char ip[16],unsigned *bits) {
    char value[80],*slash,*end;size_t n=strlen(text);
    if(n>=sizeof(value))return 0;
    memcpy(value,text,n+1);slash=strchr(value,'/');if(!slash)return 0;
    *slash++=0;unsigned long mask=strtoul(slash,&end,10);
    if(!*slash||*end||mask<1||mask>128||inet_pton(AF_INET6,value,ip)!=1)return 0;
    *bits=(unsigned)mask;return 1;
}
static int prefix_match(const unsigned char *address,const unsigned char *prefix,unsigned bits) {
    unsigned bytes=bits/8,rest=bits%8;
    return !memcmp(address,prefix,bytes) &&
       (!rest || (address[bytes]>>(8-rest))==(prefix[bytes]>>(8-rest)));
}
static int source_matches(const void *attrs,int size,const unsigned char *prefix,unsigned bits) {
    const struct nlattr *src=nested(nested(find_attr(attrs,size,CTA_TUPLE_ORIG),CTA_TUPLE_IP),CTA_IP_V6_SRC);
    return src&&src->nla_len==NLA_HDRLEN+16&&prefix_match((const unsigned char *)src+NLA_HDRLEN,prefix,bits);
}
static int copy_attr(struct entry *e,const struct nlattr *a) {
    if(!a)return 1;
    unsigned n=NLA_ALIGN(a->nla_len);
    if(e->length+n>sizeof(e->attrs))return 0;
    memcpy(e->attrs+e->length,a,a->nla_len);
    memset(e->attrs+e->length+a->nla_len,0,n-a->nla_len);e->length+=n;return 1;
}
static int v6_delete_prefix(const char *text) {
    unsigned char prefix[16];unsigned bits;
    if(!prefix_parse(text,prefix,&bits))return 2;
    int fd=socket(AF_NETLINK,SOCK_RAW,NETLINK_NETFILTER);
    if(fd<0)return 1;
    struct timeval timeout={3,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
    struct sockaddr_nl local={.nl_family=AF_NETLINK},kernel={.nl_family=AF_NETLINK};
    if(bind(fd,(void *)&local,sizeof(local))){close(fd);return 1;}
    struct {struct nlmsghdr h;struct nfgenmsg g;} req={
      .h={.nlmsg_len=NLMSG_LENGTH(sizeof(struct nfgenmsg)),.nlmsg_type=(NFNL_SUBSYS_CTNETLINK<<8)|IPCTNL_MSG_CT_GET,
          .nlmsg_flags=NLM_F_REQUEST|NLM_F_DUMP,.nlmsg_seq=50},
      .g={.nfgen_family=AF_INET6,.version=NFNETLINK_V0}};
    struct entry *first=NULL,**tail=&first;unsigned count=0;int result=1,done=0;
    if(sendto(fd,&req,req.h.nlmsg_len,0,(void *)&kernel,sizeof(kernel))<0)goto finish;
    unsigned char buffer[32768];ssize_t size;
    while(!done&&(size=recv(fd,buffer,sizeof(buffer),0))>0){
      for(struct nlmsghdr *h=(void *)buffer;NLMSG_OK(h,size);h=NLMSG_NEXT(h,size)){
        if(h->nlmsg_seq!=50)continue;
        if(h->nlmsg_flags&NLM_F_DUMP_INTR)goto finish;
        if(h->nlmsg_type==NLMSG_DONE){done=1;break;}
        if(h->nlmsg_type==NLMSG_ERROR)goto finish;
        if(h->nlmsg_len<NLMSG_LENGTH(sizeof(struct nfgenmsg)))goto finish;
        struct nfgenmsg *g=NLMSG_DATA(h);
        if(g->nfgen_family!=AF_INET6)continue;
        void *attrs=(char *)g+NLMSG_ALIGN(sizeof(*g));
        int len=h->nlmsg_len-NLMSG_LENGTH(sizeof(*g));
        if(!source_matches(attrs,len,prefix,bits))continue;
        if(++count>32768)goto finish;
        struct entry *e=calloc(1,sizeof(*e));if(!e)goto finish;
        if(!copy_attr(e,find_attr(attrs,len,CTA_TUPLE_ORIG))||
           !copy_attr(e,find_attr(attrs,len,CTA_ZONE))||!copy_attr(e,find_attr(attrs,len,CTA_ID))){free(e);goto finish;}
        *tail=e;tail=&e->next;
      }
    }
    if(!done)goto finish;
    unsigned seq=51;
    for(struct entry *e=first;e;e=e->next,seq++){
      struct {struct nlmsghdr h;struct nfgenmsg g;unsigned char a[1200];} del={0};
      del.h.nlmsg_len=NLMSG_LENGTH(sizeof(del.g))+e->length;
      del.h.nlmsg_type=(NFNL_SUBSYS_CTNETLINK<<8)|IPCTNL_MSG_CT_DELETE;
      del.h.nlmsg_flags=NLM_F_REQUEST|NLM_F_ACK;del.h.nlmsg_seq=seq;
      del.g.nfgen_family=AF_INET6;del.g.version=NFNETLINK_V0+1;
      memcpy(del.a,e->attrs,e->length);
      if(sendto(fd,&del,del.h.nlmsg_len,0,(void *)&kernel,sizeof(kernel))<0)goto finish;
      int ack=0;
      while(!ack&&(size=recv(fd,buffer,sizeof(buffer),0))>0){
        for(struct nlmsghdr *h=(void *)buffer;NLMSG_OK(h,size);h=NLMSG_NEXT(h,size)){
          if(h->nlmsg_seq!=seq||h->nlmsg_type!=NLMSG_ERROR)continue;
          if(h->nlmsg_len<NLMSG_LENGTH(sizeof(struct nlmsgerr)))goto finish;
          int err=((struct nlmsgerr *)NLMSG_DATA(h))->error;
          if(err&&err!=-ENOENT)goto finish;
          ack=1;
        }
      }
      if(!ack)goto finish;
    }
    result=0;
finish:
    while(first){struct entry *next=first->next;free(first);first=next;}
    close(fd);return result;
}
static int filter_selftest(void) {
    unsigned char p[16],a[16];unsigned bits;
    if(!prefix_parse("2001:db8::/64",p,&bits)||bits!=64||inet_pton(AF_INET6,"2001:db8::1234",a)!=1)return 1;
    if(!prefix_match(a,p,bits))return 1;
    if(!prefix_parse("2001:db9::/64",p,&bits)||prefix_match(a,p,bits))return 1;
    if(prefix_parse("::/0",p,&bits)||prefix_parse("invalid/64",p,&bits))return 1;
    unsigned char attrs[28]={0};struct nlattr *tuple=(void *)attrs,*ip=(void *)(attrs+4),*src=(void *)(attrs+8);
    tuple->nla_type=CTA_TUPLE_ORIG|NLA_F_NESTED;tuple->nla_len=28;
    ip->nla_type=CTA_TUPLE_IP|NLA_F_NESTED;ip->nla_len=24;
    src->nla_type=CTA_IP_V6_SRC;src->nla_len=20;memcpy(attrs+12,a,16);
    prefix_parse("2001:db8::/64",p,&bits);
    if(!source_matches(attrs,sizeof(attrs),p,bits))return 1;
    prefix_parse("2001:db9::/64",p,&bits);
    return source_matches(attrs,sizeof(attrs),p,bits)?1:0;
}

int main(int argc, char **argv) {
    const char *name=strrchr(argv[0],'/');name=name?name+1:argv[0];
    if(!strcmp(name,"timeout"))return limited_run(argc,argv);
    struct request r; int family = AF_INET; uint32_t mark = 0; char *end;
    if(argc==3&&!strcmp(argv[1],"ipv6"))return v6_delete_prefix(argv[2]);
    if (argc != 2) return 2;
    if(!strcmp(argv[1],"--filter-selftest"))return filter_selftest();
    if (!strcmp(argv[1], "--selftest")) {
        make_request(&r, AF_INET, 0x20000);
        if (r.header.nlmsg_len != 36 || r.gen.nfgen_family != AF_INET || r.gen.version != 1) return 1;
        make_request(&r, AF_INET6, 0);
        return r.header.nlmsg_len == 20 && r.gen.nfgen_family == AF_INET6 && r.gen.version == 1 ? 0 : 1;
    }
    if (!strcmp(argv[1], "ipv6")) return 2;
    {
        unsigned long value = strtoul(argv[1], &end, 0);
        if (!*argv[1] || *end || value < 0x10000 || value > 0xfc0000 || (value & ~0xff0000UL)) return 2;
        mark = (uint32_t)value;
    }
    make_request(&r, family, mark);
    int fd = socket(AF_NETLINK, SOCK_RAW, NETLINK_NETFILTER);
    if (fd < 0) { perror("conntrack socket"); return 1; }
    struct timeval timeout = {3, 0};
    if (setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout))) { close(fd); return 1; }
    struct sockaddr_nl local = { .nl_family = AF_NETLINK }, kernel = { .nl_family = AF_NETLINK };
    if (bind(fd, (void *)&local, sizeof(local)) ||
        sendto(fd, &r, r.header.nlmsg_len, 0, (void *)&kernel, sizeof(kernel)) < 0) {
        perror("conntrack request"); close(fd); return 1;
    }
    unsigned char reply[8192]; ssize_t size;
    while ((size = recv(fd, reply, sizeof(reply), 0)) > 0) {
        for (struct nlmsghdr *h = (void *)reply; NLMSG_OK(h, size); h = NLMSG_NEXT(h, size)) {
            if (h->nlmsg_seq != 1 || h->nlmsg_type != NLMSG_ERROR ||
                h->nlmsg_len < NLMSG_LENGTH(sizeof(struct nlmsgerr))) continue;
            struct nlmsgerr *e = NLMSG_DATA(h); close(fd);
            if (e->error) { errno = -e->error; perror("conntrack delete"); return 1; }
            return 0;
        }
    }
    perror("conntrack ACK"); close(fd); return 1;
}
