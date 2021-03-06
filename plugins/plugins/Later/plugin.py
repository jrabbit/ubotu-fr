###
# Copyright (c) 2004, Jeremiah Fincher
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
###

import csv
import time

import supybot.log as log
import supybot.conf as conf
import supybot.utils as utils
from supybot.commands import *
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks


class Later(callbacks.Plugin):
    """Used to do things later; currently, it only allows the sending of
    nick-based notes.  Do note (haha!) that these notes are *not* private
    and don't even pretend to be; if you want such features, consider using the
    Note plugin."""
    def __init__(self, irc):
        self.__parent = super(Later, self)
        self.__parent.__init__(irc)
        self._notes = ircutils.IrcDict()
        self.wildcards = []
        self.filename = conf.supybot.directories.data.dirize('Later.db')
        self._openNotes()

    def die(self):
        self._flushNotes()

    def _flushNotes(self):
        fd = utils.file.AtomicFile(self.filename)
        writer = csv.writer(fd)
        for (nick, notes) in self._notes.iteritems():
            for (time, whence, text) in notes:
                writer.writerow([nick, time, whence, text])
        fd.close()

    def _openNotes(self):
        try:
            fd = file(self.filename)
        except EnvironmentError, e:
            self.log.warning('Couldn\'t open %s: %s', self.filename, e)
            return
        reader = csv.reader(fd)
        for (nick, time, whence, text) in reader:
            self._addNote(nick, whence, text, at=float(time), maximum=0)
        fd.close()

    def _timestamp(self, when):
        #format = conf.supybot.reply.format.time()
        diff = time.time() - when
        try:
            return utils.timeElapsed(diff, seconds=False) + ' ago'
        except ValueError:
            return 'just now'

    def _addNote(self, nick, whence, text, at=None, maximum=None):
        if at is None:
            at = time.time()
        if maximum is None:
            maximum = self.registryValue('maximum')
        try:
            notes = self._notes[nick]
            if maximum and len(notes) >= maximum:
                raise ValueError
            else:
                notes.append((at, whence, text))
        except KeyError:
            self._notes[nick] = [(at, whence, text)]
        if '?' in nick or '*' in nick and nick not in self.wildcards:
            self.wildcards.append(nick)
        self._flushNotes()

    def tell(self, irc, msg, args, nick, text):
        """<nick> <text>

        Tells <nick> <text> the next time <nick> is in seen.  <nick> can
        contain wildcard characters, and the first matching nick will be
        given the note.
        """
        if ircutils.strEqual(nick, irc.nick):
            irc.error('I can\'t send notes to myself.')
            return
        try:
            self._addNote(nick, msg.nick, text)
            irc.replySuccess()
        except ValueError:
            irc.error('That person\'s message queue is already full.')
    tell = wrap(tell, ['something', 'text'])

    def notes(self, irc, msg, args, nick):
        """[<nick>]

        If <nick> is given, replies with what notes are waiting on <nick>,
        otherwise, replies with the nicks that have notes waiting for them.
        """
        if nick:
            if nick in self._notes:
                notes = [self._formatNote(when, whence, note)
                         for (when, whence, note) in self._notes[nick]]
                irc.reply(format('%L', notes))
            else:
                irc.error('I have no notes for that nick.')
        else:
            nicks = self._notes.keys()
            if nicks:
                utils.sortBy(ircutils.toLower, nicks)
                irc.reply(format('I currently have notes waiting for %L.',
                                 nicks))
            else:
                irc.error('I have no notes waiting to be delivered.')
    notes = wrap(notes, [additional('something')])

    def remove(self, irc, msg, args, nick):
        """<nick>

        Removes the notes waiting on <nick>.
        """
        try:
            del self._notes[nick]
            self._flushNotes()
            irc.replySuccess()
        except KeyError:
            irc.error('There were no notes for %r' % nick)
    remove = wrap(remove, [('checkCapability', 'admin'), 'something'])

    def doPrivmsg(self, irc, msg):
        notes = self._notes.pop(msg.nick, [])
        # Let's try wildcards.
        removals = []
        for wildcard in self.wildcards:
            if ircutils.hostmaskPatternEqual(wildcard, msg.nick):
                removals.append(wildcard)
                notes.extend(self._notes.pop(wildcard))
            for removal in removals:
                self.wildcards.remove(removal)
        if notes:
            irc = callbacks.SimpleProxy(irc, msg)
            private = self.registryValue('private')
            for (when, whence, note) in notes:
                s = self._formatNote(when, whence, note)
                irc.reply(s, private=private)
            self._flushNotes()

    def _formatNote(self, when, whence, note):
        return 'Sent %s: <%s> %s' % (self._timestamp(when), whence, note)



Class = Later

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
