#!/usr/bin/env python3

import os
from pathlib import Path
import re
import subprocess
import sys
import readconf
import ipaddress
import collections
import time
import lxc

#
# Make and start containers
#

HOME = "/home/student/"
BASE = "base"

def init_dns():
    with open("/etc/resolv.conf", "w") as fd:
        fd.write("nameserver 8.8.8.8\n")

def save_hostname(name):
    def f():
        with open("/etc/hostname", "w") as fd:
            fd.write("%s\n" % name)
    return f

def link_to_hostaddr(link):
    return '10.1.%s.4' % link

def link_to_subnet(link):
    return '10.1.%s.0/24' % link

def info(c):
    r = ""
    if c.running:
        ps = subprocess.Popen(["lxc-attach", "-n", c.name, "--", "ps", "-v"],
                              stdout=subprocess.PIPE).communicate()[0]
        ps = ps.decode('utf-8')
        ps = ps.split('\n')
        pat = re.compile(r'ps -v|/sbin/agetty')
        for p in ps:
            if pat.search(p):
                continue
            if p == '':
                continue
            r += "\n" + p
    ipv4 = "unknown"
    try:
        ipv4 = c.get_config_item('lxc.net.0.ipv4.address')
    except KeyError:
        pass
    return "%s: %s, IP %s%s\n" % (c.name, c.state, ipv4, r)

class Container():
    def __init__(self, conf, name, svcs, globalconf):
        self.c = lxc.Container(name)
        self.conf = conf
        self.name = name
        self.svcs = svcs
        self.globalconf = globalconf

        if name == "base":
            return

        if not self.c.defined:
            self.make_container()

        if not self.c.start():
            self.errormsg("Failed to start the container")
            sys.exit(1)

        self.configure_fw()

        self.infomsg("Copying files")
        self.dup_dir(".", excludes=["./zoobar/db"])

    def errormsg(self, msg):
        print("%s: ERROR: %s" % (self.name, msg))

    def infomsg(self, msg):
        print("%s: %s" % (self.name, msg))

    def configure_fw(self):
        rules = self.conf.lookup('fwrule')
        if rules is None:
            return
        if not isinstance(rules, list):
            rules = [rules]
        for r in rules:
            self.configure_fw_rule(r)

    def configure_fw_rule(self, r_orig):
        r = r_orig.split(' ')
        for index, item in enumerate(r):
            if self.globalconf.isservice(item):
                r[index] = link_to_subnet(self.globalconf.lookup(item, 'lxcbr'))
            if ',' in item:
                i = item.split(',')
                for index1, item1 in enumerate(i):
                    if self.globalconf.isservice(item1):
                        i[index1] = link_to_subnet(self.globalconf.lookup(item1, 'lxcbr'))
                i = ",".join(i)
                r[index] = i
        res = self.run_cmd(["/sbin/iptables", "-A", "INPUT"] + r)
        if res != 0:
            self.errormsg("Failed to configure firewall rule %s" % r_orig)

    def make_base(self):
        os.makedirs('%s/.local/share/lxc' % HOME, exist_ok=True)
        self.infomsg("Creating container")
        if not self.c.create("download", 0,
                        { "dist": "debian",
                          "release": "stretch",
                          "arch": "amd64" }):
            self.errormsg("Could not download initial container image")
            sys.exit(1)

        ## Base container gets a special network setup
        self.configure_network('0')

        self.infomsg("Configuring")
        self.configure_base()

    def configure_base(self):
        if not self.c.start():
            self.errormsg("Failed to start")
            sys.exit(1)

        self.attach_wait(init_dns)

        pkgs = ["python3", "python3-lxc",
                "python3-flask-sqlalchemy", "python3-cryptography",
                "psmisc", "iputils-ping", "iptables",
                ]

        # update path to include sbin so that apt install will work
        path = "/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games:/snap/bin:/usr/local/sbin:/sbin:/usr/sbin"
        ev = ["PATH=%s" % path]

        while True:
            self.run_cmd(["bash", "-c", "systemctl stop networking 2>/dev/null"])
            r = self.run_cmd(["bash", "-c", "systemctl is-system-running 2>/dev/null | egrep -q '(degraded|running)'"])
            if r == 0:
                break
            time.sleep(1)

        # install packages for zoobar
        self.run_cmd(["apt-get", "update"], extra_env_vars=ev)
        self.run_cmd(["apt-get", "install", "-y"] + pkgs, extra_env_vars=ev)

        # directory for zook
        self.run_cmd(["mkdir", HOME])

        # LXC brings up the container's network interface on its own
        self.run_cmd(["systemctl", "disable", "networking"])
        self.run_cmd(["systemctl", "disable", "systemd-resolved"])
        self.attach_wait(init_dns)

        if not self.c.stop():
            self.errormsg("Failed to stop")

    def configure_network(self, link):
        ipv4 = link_to_hostaddr(link)
        addr = ipaddress.ip_address(ipv4)
        self.c.set_config_item('lxc.net.0.type', 'veth')
        self.c.set_config_item('lxc.net.0.link', 'lxcbr%s' % link)
        self.c.set_config_item('lxc.net.0.flags', 'up')
        self.c.set_config_item('lxc.net.0.hwaddr', '68:58:%02x:%02x:%02x:%02x' % tuple(addr.packed))
        self.c.set_config_item('lxc.net.0.ipv4.address', '%s/24' % ipv4)
        self.c.set_config_item('lxc.net.0.ipv4.gateway', 'auto')
        self.c.save_config()

    def make_container(self):
        b = lxc.Container(BASE)
        if not b.defined:
            bc = Container(None, BASE, None, self.globalconf)
            bc.make_base()
            b = lxc.Container(BASE)

        self.infomsg("Creating container")
        c = b.clone(self.name, bdevtype="overlayfs", flags=lxc.LXC_CLONE_SNAPSHOT)
        if not c:
            self.errormsg("Clone failed")
            sys.exit(1)

        self.c = c
        self.configure_network(self.conf.lookup('lxcbr'))

        self.c.start()
        self.attach_wait(save_hostname(self.name))
        if not self.c.stop():
            self.errormsg("Failed to stop")

    def zooksvc(self, k):
        self.infomsg("Running zooksvc.py")
        self.run_cmd(["%s/zooksvc.py" % HOME, k])

    def attach_wait(self, *args, **kwargs):
        filter = subprocess.Popen(["sed", "-e", "s,^,%s: ," % self.name], stdin=subprocess.PIPE)
        return self.c.attach_wait(*args, stdout=filter.stdin, stderr=filter.stdin, **kwargs)

    def run_cmd(self, cmd, extra_env_vars=[]):
        return self.attach_wait(lxc.attach_run_command, cmd, extra_env_vars=extra_env_vars)

    def copy_file(self, d, name):
        p1 = subprocess.Popen(["tar", "-c", "-C", d, name], stdout=subprocess.PIPE)
        p2 = subprocess.Popen(['lxc-attach', '-n', self.name, '--', 'tar', 'xf', '-', "-C", HOME], stdin=p1.stdout)
        p2.wait()
        
    def dup_dir(self, host_dir, excludes=[]):
        exclude_args = []
        for e in excludes:
            exclude_args.append('--exclude=%s' % e)
        p1 = subprocess.Popen(["tar"] + exclude_args + ["-c", host_dir], stdout=subprocess.PIPE)
        p2 = subprocess.Popen(['lxc-attach', '-n', self.name, '--', 'tar', 'xf', '-', "-C", HOME], stdin=p1.stdout)
        p2.wait()

def boot(k=None):
    ct = readconf.read_conf()
    # check for link dups
    svcs_on_link = collections.defaultdict(list)
    for s in ct.svcs():
        link = ct.lookup(s, 'lxcbr')
        if link is None:
            raise Exception("Missing lxcbr link for container %s" % s)
        if link not in [str(i) for i in range(0, 10)]:
            raise Exception("Unknown lxcbr link %s for container %s" % (link, s))
        svcs_on_link[link].append(s)
    for link in svcs_on_link:
        if len(svcs_on_link[link]) > 1:
            raise Exception("More than one container on lxcbr%s: %s" % (link, svcs_on_link[link]))
    if k == None:
        for k in ct.svcs():
            c = Container(ct.conf(k), k, ct.svcs(), ct)
            c.zooksvc(k)
    else:
        c = Container(ct.conf(k), k, ct.svcs(), ct)
        c.zooksvc(k)

def shutdown(k=None):
    ct = readconf.read_conf()
    if k == None:
        for k in ct.svcs():
            c = lxc.Container(k)
            c.stop()
    else:
        c = lxc.Container(k)
        c.stop()

def clean(k=None):
    shutdown(k)
    ct = readconf.read_conf()
    if k == None:
        for k in ct.svcs():
            c = lxc.Container(k)
            c.shutdown(timeout=0)
            c.destroy()
        for k in lxc.list_containers():
            c = lxc.Container(k)
            c.shutdown(timeout=0)
            c.destroy()
        c = lxc.Container(BASE)
        c.shutdown(timeout=0)
        c.destroy()
    else:
        c = lxc.Container(k)
        c.destroy()

def ps(k=None):
    ct = readconf.read_conf()
    if k == None:
        for k in sorted(ct.svcs()):
            c = lxc.Container(k)
            print(info(c))
    else:
        c = lxc.Container(k)
        print(info(c))
