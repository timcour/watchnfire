#!/usr/bin/env python

# Copyright (C) 2013 by Tim Courrejou. All rights reserved

#TODO: config file?
#TODO: unit testing
#TODO: docs

import fsevents
import logging
import os
import re
import signal
import sys
import time

from fsevents import Observer, Stream
from subprocess import Popen, PIPE
from threading import Timer

def get_logger(level=logging.INFO):
    logger = logging.getLogger()
    handler = logging.StreamHandler(stream=sys.stdout)
    formatter = logging.Formatter('%(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(level)
    return logger

log = get_logger()

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

class ProcExecutionStats(object):
    def __init__(self, command=None):
        self.command = command
        self.ti = time.time()
        self.tf = None

    def execution_completed(self):
        self.tf = time.time()

    def __str__(self):
        time_ago = (time.time() - self.tf) if self.tf else -1
        return "%s last run: %ss ago..." % (self.command, int(time_ago))

class EventTriggerManager(object):
    def __init__(self, triggers, queue_execution_wait=0.2):
        self.triggers = triggers
        self.observer = Observer()
        self.last_execution_stats = None

        self.firing_queue = []
        self.firing_wait = queue_execution_wait
        self.firing_queue_thread = Timer(self.firing_wait, self.execute_firing_queue)
        self.is_executing_firing_queue = False

    def queue_firing_trigger(self, trigger):
        if trigger not in self.firing_queue:
            log.debug("adding %s to firing_queue" % trigger)
            self.firing_queue.insert(0, trigger)

        if (not self.is_executing_firing_queue
            and self.firing_queue_thread.is_alive()):
            log.debug("received another queue request, canceling timer")
            self.firing_queue_thread.cancel()
            self.firing_queue_thread.join()

        if not self.firing_queue_thread.is_alive():
            self.firing_queue_thread = Timer(self.firing_wait, self.execute_firing_queue)
            log.debug("starting new timer")
            self.firing_queue_thread.start()

    def execute_firing_queue(self):
        log.debug("executing firing queue")
        if self.is_executing_firing_queue:
            log.error("execution in progress")
            return
        self.is_executing_firing_queue = True
        while len(self.firing_queue):
            pt = self.firing_queue.pop()
            stat = ProcExecutionStats(command=pt.command)
            pt.fire()
            stat.execution_completed()
            self.last_execution_stats = stat
        self.is_executing_firing_queue = False

    def start(self, prefire=True):
        self.observer.start()
        for pt in self.triggers:
            log.debug("scheduling stream: %s" % pt)
            pt.schedule_execution = self.queue_firing_trigger
            self.observer.schedule(pt.stream)
        if prefire:
            for pt in self.triggers:
                self.queue_firing_trigger(pt)

    def stop(self):
        for pt in self.triggers:
            self.observer.unschedule(pt.stream)

            # remove pending triggers from queue.  This must be done
            # after unscheduling the stream but before killing a
            # potential running process to avoid a race condition.
            self.firing_queue = []

            # kill any process being run by the trigger now that it
            # cannot be rescheduled
            pt.killfire()

        self.observer.stop()
        self.observer.join()

class PathTrigger(object):
    def __init__(self, path, extensions, command, ignore_file_pattern="^\.#.*$"):
        self.path = path
        self.regexp = re.compile(".*\.(%s)$" % '|'.join(extensions.split(',')))
        self.file_ignore_regexp = re.compile(ignore_file_pattern)
        self.command = command
        self.stream = Stream(self.handle_event, self.path, file_events=True)
        self.proc = None

        # should be set by a user of this object
        self.schedule_execution = lambda x: None

    def fire(self):
        log.info("---> firing: %s" % self.command)
        self.proc = Popen(self.command, shell=True, stdout=PIPE, stderr=PIPE)
        log.info("%s" % self.proc.stdout.read())
        for stream in map(lambda x: getattr(self.proc, x), ['stdin', 'stdout', 'stderr']):
            if stream:
                stream.close()
        self.proc.wait()
        self.proc = None

    def killfire(self):
        if self.proc:
            self.proc.kill()

    def handle_event(self, event):
        log.debug(event.name, event.cookie, get_event_flags(event.mask))
        event_path = os.path.dirname(event.name)
        event_file = os.path.basename(event.name)
        if (self.extension_matches(event.name)
            and not self.should_ignore_file(event_file)):
            self.schedule_execution(self)

    def extension_matches(self, filepath):
        log.debug('does "%s" match %s' % (self.regexp.pattern, filepath))
        return bool(self.regexp.match(filepath))

    def should_ignore_file(self, filename):
        log.debug('does "%s" match %s' % (self.file_ignore_regexp.pattern, filename))
        return bool(self.file_ignore_regexp.match(filename))

    def __str__(self):
        return '<%s {path: "%s", regexp: "%s", command: "%s"}>' % (
            self.__class__.__name__, self.path, self.regexp.pattern, self.command)
    def __repr__(self):
        return str(self)

class FSTriggerRunner(object):
    def __init__(self, event_manager):
        self.trigman = event_manager
        signal.signal(signal.SIGINT, self.sigint_handler)

    def start(self, prefire=True):
        self.trigman.start(prefire=prefire)
        while True:
            if (self.trigman.last_execution_stats
                and not self.trigman.is_executing_firing_queue):
                sys.stdout.write("\r%s" % self.trigman.last_execution_stats)
                sys.stdout.flush()
            time.sleep(1)

    def sigint_handler(self, signal, frame):
        log.info('SIGINT caught, exiting...')
        self.trigman.stop()
        sys.exit(0)

if __name__=='__main__':
    from optparse import OptionParser
    usage = "usage: %prog [options] path extensions command ..."
    parser = OptionParser(usage=usage)
    parser.add_option("-v", "--verbose", action="store_true",
                      help=("verbose output"), dest="verbose")
    parser.add_option("-f", "--prefire", action="store_true",
                      help=("run trigger commands on start"), dest="prefire")
    (options, args) = parser.parse_args()

    log.setLevel(logging.DEBUG if options.verbose else logging.INFO)

    triggers = []
    log.debug("trigger args: %s" % args)
    for i in xrange(0, len(args), 3):
        tmp = args[i:i+3]
        pt = PathTrigger(*tmp)
        triggers.append(pt)

    etm = EventTriggerManager(triggers)
    FSTriggerRunner(etm).start(prefire=options.prefire)
