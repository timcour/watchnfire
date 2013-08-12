#!/usr/bin/env python

# Copyright (C) 2013 by Tim Courrejou. All rights reserved

import fsevents
import os
import re
import sys
from subprocess import Popen, PIPE
from fsevents import Observer, Stream

from pprint import pformat as pf

def is_flag(testval):
    if not isinstance(testval, int):
        return False
    if testval > 0 and (testval & (testval-1)) == 0:
        return True
    return False

def get_all_flags(obj):
    tbl = {}
    for attr in dir(obj):
        tbl[attr] = getattr(obj, attr)
    return dict(filter(lambda kv: is_flag(kv[1]), tbl.iteritems()))

ALL_FSEVENT_FLAGS = get_all_flags(fsevents)

def get_event_flags(mask):
    is_set = lambda flag: bool(mask & flag)
    flags = dict(filter(lambda flag: is_set(flag[1]), ALL_FSEVENT_FLAGS.iteritems()))
    return flags.keys()

class FSTrigger(object):
    def __init__(self, triggers):
        self.observer = Observer()
        self.firing_queue = []

    def queue_firing_trigger(self, trigger):
        trigger.fire()
        if trigger not in self.firing_queue:
            print "adding %s to firing_queue" % trigger
            self.firing_queue.insert(0, trigger)

    def run_fsevents_demo(self):
        self.observer.start()
        for pt in triggers:
            print "scheduling stream: %s" % pt
            pt.schedule_execution = self.queue_firing_trigger
            self.observer.schedule(pt.stream)

class PathTrigger(object):
    def __init__(self, path, extensions, command, ignore_file_pattern="^\.#.*$"):
        self.path = path
        self.regexp = re.compile(".*\.(%s)$" % '|'.join(extensions.split(',')))
        self.command = command
        self.stream = Stream(self.handle_event, self.path, file_events=True)

        self.file_ignore_regexp = re.compile(ignore_file_pattern)

        # should be set by a user of this object
        self.schedule_execution = lambda x: None

    def fire(self):
        print "---> firing: %s" % self.command
        proc = Popen(self.command, shell=True, stdout=PIPE, stderr=PIPE)
        print proc.stdout.read()
        for stream in map(lambda x: getattr(proc, x), ['stdin', 'stdout', 'stderr']):
            if stream:
                stream.close()
        proc.wait()

    def handle_event(self, event):
        print event.name, event.cookie, get_event_flags(event.mask)
        event_path = os.path.dirname(event.name)
        event_file = os.path.basename(event.name)
        if (self.extension_matches(event.name)
            and not self.should_ignore_file(event_file)):
            self.schedule_execution(self)

    def extension_matches(self, filepath):
        print 'does "%s" match %s' % (self.regexp.pattern, filepath)
        return bool(self.regexp.match(filepath))

    def should_ignore_file(self, filename):
        print 'does "%s" match %s' % (self.file_ignore_regexp.pattern, filename)
        return bool(self.file_ignore_regexp.match(filename))

    def __str__(self):
        return '<%s {path: "%s", regexp: "%s", command: "%s"}>' % (
            self.__class__.__name__, self.path, self.regexp.pattern, self.command)
    def __repr__(self):
        return str(self)

if __name__=='__main__':
    from optparse import OptionParser
    usage = "usage: %prog [options] path extensions command ..."
    parser = OptionParser(usage=usage)
    parser.add_option("-v", "--verbose", action="store_true",
                      help=("verbose output"))
    (options, args) = parser.parse_args()

    triggers = []
    print args
    for i in xrange(0, len(args), 3):
        tmp = args[i:i+3]
        pt = PathTrigger(*tmp)
        triggers.append(pt)

    fst = FSTrigger(triggers)
    fst.run_fsevents_demo()