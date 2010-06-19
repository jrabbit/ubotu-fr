###
# Copyright (c) 2002-2005, Jeremiah Fincher
# Copyright (c) 2009, James Vega
# Copyright (c) 2010, Nicolas Coevoet
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

import os
import time
import string
import re
import sys
import operator

import supybot.irclib as irclib
import supybot.log as log
import supybot.conf as conf
import supybot.ircdb as ircdb
import supybot.utils as utils
from supybot.commands import *
import supybot.ircmsgs as ircmsgs
import supybot.schedule as schedule
import supybot.callbacks as callbacks
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
import supybot.commands as commands
import socket
import random

try:
    import sqlite
except ImportError:
    raise callbacks.Error, 'You need to have PySQLite installed to use this plugin. \
        Download it at <http://pysqlite.org/>'

class SpamQueue(object):
    timeout = 0
    def __init__(self, timeout=None, queues=None):
        if timeout is not None:
            self.timeout = timeout
        if queues is None:
            queues = ircutils.IrcDict()
        self.queues = queues
    
    def __repr__(self):
        return 'SpamQueue(timeout=%r,queues=%s)' % (self.timeout,repr(self.queues))

    def reset (self,data):
        q = self._getQueue(data,insert=False)
        if q is not None:
            q.reset()
            key = self.key(data)
            self.queues[key] = q
        
    def key (self,data):
        return data[0]

    def getTimeout(self):
        if callable(self.timeout):
            return self.timeout()
        else:
            return self.timeout

    def _getQueue(self,data,insert=True):
        try:
            return self.queues[self.key(data)]
        except KeyError:
            if insert:
                getTimeout = lambda : self.getTimeout()
                q = utils.structures.TimeoutQueue(getTimeout)
                self.queues[self.key(data)] = q
                return q
            else:
                return None

    def enqueue(self,data,what=None):
        if what is None:
            what = data
        q = self._getQueue(data)
        q.enqueue(what)

    def len (self,data):
        q = self._getQueue(data,insert=False)
        if q is not None:
            return len(q)
        else:
            return 0

    def has (self,data,what):
        q = self._getQueue(data,insert=False)
        if q is not None:
            if what is None:
                what = data
            for elt in q:
                if elt == what:
                    return True
        return False
        
class Chan (object):
    def __init__(self):
        object.__init__(self)
        self.nicks = {}
        self.logChannel = None
        self.logSize = 20
        self.opChannel = None
        self.evadeBanCheck = False
        self.evadeKickMessage = ''
        self.evadeBanDuration = -1
        self.synchro = False
        self.schedules = {}
        self.activeBans = {}
        self.pendingBans = {}
        self.activeQuiets = {}
        self.pendingQuiets = {}
        self.removed = {}
        self.floodCheck = False
        self.floodPermit = -1
        self.floodLife = -1
        self.floodQuietDuration = -1
        self.floodQueue = None
        self.lowFloodCheck = False
        self.lowFloodPermit = -1
        self.lowFloodLife = -1
        self.lowFloodQuietDuration = -1
        self.lowFloodQueue = None
        self.floodMessage = None
        self.repeatCheck = False
        self.repeatQueue = None
        self.repeatPermit = -1
        self.repeatLife = -1
        self.repeatQuietDuration = -1
        self.repeatMessage = None
        self.highlightCheck = False
        self.highlightPermit = -1
        self.highlightMessage = None
        self.highlightQuietDuration = -1
        
        self.noticeCheck = False
        self.noticeQueue = None
        self.noticePermit = -1
        self.noticeLife = -1
        self.noticeQuietDuration = -1
        self.noticeMessage = None
        
        self.badUserQueue = None
        self.badUserLife = -1
        self.badUserPermit = -1
        self.badUserMessage = None
        self.badUserBanDuration = -1
        
        self.massjoinCheck = False
        self.massjoinQueue = None
        self.massjoinLife = -1
        self.massjoinPermit = -1
        self.massjoinMode = None
        self.massjoinUnMode = None
        self.massjoinDuration = -1
        
        self.cycleCheck = False
        self.cycleQueue = None
        self.cyclePermit = -1
        self.cycleLife = -1
        self.cycleBanDuration = -1
        
        self.attacks = SpamQueue(60)
        self.netsplit = False
        self.regexps = []
        self.warnLife = -1
        self.warnSchedule = None
        
        self.commandCheck = False
        self.commandPermit = -1
        self.commandLife = -1
        self.commandQueue = None
        self.commandDisableDuration = -1
        
class Nick (object):
    def __init__(self):
        object.__init__(self)
        self.nick = None
        self.host = None
        self.mask = None
        self.logs = utils.structures.smallqueue()
        self.warns = 0


def getmask (irc,nickormask):
    if ircutils.isUserHostmask(nickormask):
        hostmask = nickormask
        return hostmask
    else:
        try:
            hostmask = irc.state.nickToHostmask(nickormask)
        except:
            return None
    return ircdb.getmask(hostmask)

def isgatewayweb(s):
    return s.find('gateway/web') != -1		

_iptohexa = {}

def iptohexa(s):
    if s in _iptohexa:
        return _iptohexa[s]
    try:
        _iptohexa[s] = ''.join(["%02X" % long(i) for i in s.split('.')])
        return _iptohexa[s]
    except:
        return None

def splitmessage(s,n):
    l = [] 
    for i in range(0, len(s), n): 
        l.append(s[i:i+n])
    return l

def getduration (text):
    duration = -1
    if len(text) > 1:
        if text.isdigit():
            try:
                duration = int(text)
            except:
                duration = -1
        else:
            multi = -1
            a = []
            if text.find('s') != -1:
                multi = 1
                a = text.split('s')
            elif text.find('m') != -1:
                a = text.split('m')
                multi = 60
            elif text.find('h') != -1:
                a = text.split('h')
                multi = 3600
            elif text.find('d') != -1:
                a = text.split('d')
                multi = 86400
            elif text.find('w') != -1:
                a = text.split('w')
                multi = 604800
            elif text.find('M') != -1:
                a = text.split('M')
                multi = 18144000
            elif text.find('Y') != -1:
                a = text.split('Y')
                multi = 6622560000
            if len(a) > 1:
                if a[0].isdigit():
                    try:
                        duration = int(a[0])*multi
                    except:
                        duration = multi
    else:
        if text.isdigit():
            try:
                duration = int(text)
            except:
                duration = -1
    return duration
    
def _purge ():
    _hostmaskToBanMask = {}
        
class Channel(callbacks.Plugin,plugins.ChannelDBHandler):
    threaded = True
    noIgnore = True
    
    def __init__(self, irc):
        self.__parent = super(Channel, self)
        self.__parent.__init__(irc)
        self.ircs = {}
        self.dbCache = {}
        self.invites = {}
        schedule.addPeriodicEvent(_purge,86400)

    def ops (self,irc,msg,args,channel,text):
        if not channel in irc.state.channels:
            return
        else:
            if not irc.state.channels[channel].synchro:
                return
        c = self._getChan(irc,channel)
        if c.opChannel in irc.state.channels:
            if not text:
                text = ''
            users = ', '.join(irc.state.channels[c.opChannel].users)
            irc.queueMsg(ircmsgs.privmsg(c.opChannel,'<%s|!OPS|%s> %s %s' % (msg.nick, channel, text, users)))
            if msg.nick != irc.nick:
                irc.replySuccess()
    ops = wrap(ops, ['channel',additional('text')])

    def notice(self, irc, msg, args, target, text):
        if ircutils.nickEqual(target,irc.nick):
            return
        if target not in irc.state.nicksToHostmasks and not ircdb.checkCapability(msg.prefix, 'owner'):
            return
        irc.reply(text, to=target, private=True, notice=True)
    notice = wrap(notice, ['owner','something', 'text'])

    def private(self, irc, msg, args, target, text):
        if target.lower() == 'me':
            target = msg.nick
        if ircutils.isChannel(target):
            irc.queueMsg(ircmsgs.privmsg(target,text))
            return
        if not ircutils.isNick(target):
            return
        if ircutils.nickEqual(target, irc.nick):
            return
        if target not in irc.state.nicksToHostmasks and not ircdb.checkCapability(msg.prefix, 'owner'):
            return
        irc.reply(text, to=target, private=True, notice=False)
    private = wrap(private, ['owner','something', 'text'])

    def _sendMsg(self, irc, msg):
        irc.sendMsg(msg)
        irc.noReply()

    def _sendMsgs(self, irc, nicks, f):
        numModes = irc.state.supported.get('modes', 1)
        for i in range(0, len(nicks), numModes):
            irc.sendMsg(f(nicks[i:i + numModes]))
        irc.noReply()

    def mode(self, irc, msg, args, channel, modes):
        """[<channel>] <mode> [<arg> ...]

        Sets the mode in <channel> to <mode>, sending the arguments given.
        <channel> is only necessary if the message isn't sent in the channel
        itself.
        """
        self._sendMsg(irc, ircmsgs.IrcMsg('MODE %s %s' % (channel, modes)))
    mode = wrap(mode, ['op', ('haveOp', 'change the mode'), 'text'])

    def limit(self, irc, msg, args, channel, limit):
        """[<channel>] [<limit>]

        Sets the channel limit to <limit>.  If <limit> is 0, or isn't given,
        removes the channel limit.  <channel> is only necessary if the message
        isn't sent in the channel itself.
        """
        if limit:
            self._sendMsg(irc, ircmsgs.mode(channel, ['+l', limit]))
        else:
            self._sendMsg(irc, ircmsgs.mode(channel, ['-l']))
    limit = wrap(limit, ['op', ('haveOp', 'change the limit'),
                        additional('nonNegativeInt', 0)])

    def key(self, irc, msg, args, channel, key):
        """[<channel>] [<key>]

        Sets the keyword in <channel> to <key>.  If <key> is not given, removes
        the keyword requirement to join <channel>.  <channel> is only necessary
        if the message isn't sent in the channel itself.
        """
        networkGroup = conf.supybot.networks.get(irc.network)
        networkGroup.channels.key.get(channel).setValue(key)
        if key:
            self._sendMsg(irc, ircmsgs.mode(channel, ['+k', key]))
        else:
            self._sendMsg(irc, ircmsgs.mode(channel, ['-k']))
    key = wrap(key, ['op', ('haveOp', 'change the keyword'),
                     additional('somethingWithoutSpaces', '')])

    def op(self, irc, msg, args, channel, nicks):
        """[<channel>] [<nick> ...]

        If you have the #channel,op capability, this will give all the <nick>s
        you provide ops.  If you don't provide any <nick>s, this will op you.
        <channel> is only necessary if the message isn't sent in the channel
        itself.
        """
        if not channel in irc.state.channels:
            irc.error("i'm not in %s" % channel)
            return
        else:
            if not irc.state.channels[channel].synchro:
                irc.error("i'm not synchronized in %s" % channel)
                return
        if not nicks:
            nicks = [msg.nick]
        def f(L):
            return ircmsgs.ops(channel, L)
        self._sendMsgs(irc, nicks, f)
    op = wrap(op, ['op', ('haveOp', 'op someone'), any('nickInChannel')])
    
    def deop(self, irc, msg, args, channel, nicks):
        """[<channel>] [<nick> ...]

        If you have the #channel,op capability, this will remove operator
        privileges from all the nicks given.  If no nicks are given, removes
        operator privileges from the person sending the message.
        """
        if not channel in irc.state.channels:
            irc.error("i'm not in %s" % channel)
            return
        else:
            if not irc.state.channels[channel].synchro:
                irc.error("i'm not synchronized in %s" % channel)
                return
        if irc.nick in nicks:
            irc.error('I cowardly refuse to deop myself.  If you really want '
                      'me deopped, tell me to op you and then deop me '
                      'yourself.', Raise=True)
        if not nicks:
            nicks = [msg.nick]
        def f(L):
            return ircmsgs.deops(channel, L)
        self._sendMsgs(irc, nicks, f)
    deop = wrap(deop, ['op', ('haveOp', 'deop someone'),
                       any('nickInChannel')])
                       
    def voice(self, irc, msg, args, channel, nicks):
        """[<channel>] [<nick> ...]

        If you have the #channel,voice capability, this will voice all the
        <nick>s you provide.  If you don't provide any <nick>s, this will
        voice you. <channel> is only necessary if the message isn't sent in the
        channel itself.
        """
        if not channel in irc.state.channels:
            return
        else:
            if not irc.state.channels[channel].synchro:
                return
        if nicks:
            if len(nicks) == 1 and msg.nick in nicks:
                capability = 'voice'
            else:
                capability = 'op'
        else:
            nicks = [msg.nick]
            capability = 'voice'
        capability = ircdb.makeChannelCapability(channel, capability)
        if ircdb.checkCapability(msg.prefix, capability):
            def f(L):
                return ircmsgs.voices(channel, L)
            a = []
            d = {}
            for nick in nicks:
                if not nick in d and not nick in irc.state.channels[channel].voices:
                    d[nick] = nick
                    a.append(nick)  
            self._sendMsgs(irc, a, f)
        else:
            irc.errorNoCapability(capability)
    voice = wrap(voice, ['channel', ('haveOp', 'voice someone'),
                         any('nickInChannel')])

    def devoice(self, irc, msg, args, channel, nicks):
        """[<channel>] [<nick> ...]

        If you have the #channel,op capability, this will remove voice from all
        the nicks given.  If no nicks are given, removes voice from the person
        sending the message.
        """
        if not channel in irc.state.channels:
            return
        else:
            if not irc.state.channels[channel].synchro:
                return
        if irc.nick in nicks:
            irc.error('I cowardly refuse to devoice myself.  If you really '
                      'want me devoiced, tell me to op you and then devoice '
                      'me yourself.', Raise=True)
        if not nicks:
            nicks = [msg.nick]
        def f(L):
            return ircmsgs.devoices(channel, L)
        a = []
        d = {}
        for nick in nicks:
            if not nick in d and nick in irc.state.channels[channel].voices:
                d[nick] = nick
                a.append(nick)  
        self._sendMsgs(irc, nicks, f)
    devoice = wrap(devoice, ['channel','voice', ('haveOp', 'devoice someone'),
                             any('nickInChannel')])

    def cycle(self, irc, msg, args, channel):
        """[<channel>]

        If you have the #channel,op capability, this will cause the bot to
        "cycle", or PART and then JOIN the channel. <channel> is only necessary
        if the message isn't sent in the channel itself.
        """
        self._delChan(irc,channel)
        self._sendMsg(irc, ircmsgs.part(channel, msg.nick))
        networkGroup = conf.supybot.networks.get(irc.network)
        self._sendMsg(irc, networkGroup.channels.join(channel))
    cycle = wrap(cycle, ['op'])

    def fpart(self, irc, msg, args, channel, nick, text):
        """[<channel>] <nick> [<reason>]"""
        if not text:
            text = msg.nick
        else:
            text = '%s - %s' % (text,msg.nick)
        irc.sendMsg(ircmsgs.IrcMsg('remove %s %s :%s' % (channel,nick,text)))
    fpart = wrap(fpart, ['channel','op', ('haveOp'),'nickInChannel',additional('text')])

    def kick(self, irc, msg, args, channel, nick, reason):
        """[<channel>] <nick> [<reason>]

        Kicks <nick> from <channel> for <reason>.  If <reason> isn't given,
        uses the nick of the person making the command as the reason.
        <channel> is only necessary if the message isn't sent in the channel
        itself.
        """
        if not channel in irc.state.channels:
            return
        else:
            if not irc.state.channels[channel].synchro:
                return
        if ircutils.strEqual(nick, irc.nick):
            irc.error('I cowardly refuse to kick myself.', Raise=True)
        if not reason:
            reason = msg.nick
        else:
            reason = '%s - %s' % (reason,msg.nick)
        kicklen = irc.state.supported.get('kicklen', sys.maxint)
        if len(reason) > kicklen:
            irc.error('The reason you gave is longer than the allowed '
                      'length for a KICK reason on this server.',
                      Raise=True)
        irc.sendMsg(ircmsgs.kick(channel, nick, reason))
    kick = wrap(kick, ['op', ('haveOp', 'kick someone'),
                       'nickInChannel', additional('text')])

    def bans(self, irc, msg, args, channel, nicks):
        """[<channel>] [<nick> ...]

        If you have the #channel,op capability, this will ban all the
        <nick>s you provide. <channel> is only necessary if the message isn't sent in the
        channel itself.
        """
        if not channel in irc.state.channels:
            return
        else:
            if not irc.state.channels[channel].synchro:
                return
        if not nicks:
            return
        def f(L):
            return ircmsgs.bans(channel, L)
        a = []
        d = {}
        c = self._getChan(irc,channel)
        now = time.time()
        for nick in nicks:
            m = getmask(irc,nick)
            if not m:
                continue
            if not m in d and not m in irc.state.channels[channel].bans:
                d[m] = m
                id = self._addban(irc,channel,msg.prefix,'b',m,0)
                self._addbanaffects(irc,channel,id,'b',m)
                c.activeBans[str(m)] = (id,msg.prefix,'b',m,now,now)
                a.append(m)   
        self._sendMsgs(irc, a, f)
    bans = wrap(bans, ['op', ('haveOp', 'ban someone'),
                         any('nickInChannel')])

    def quiets(self, irc, msg, args, channel, nicks):
        """[<channel>] [<nick> ...]

        If you have the #channel,op capability, this will quiet all the
        <nick>s you provide. <channel> is only necessary if the message isn't sent in the
        channel itself.
        """
        if not nicks or not channel in irc.state.channels:
            return
        else:
            if not irc.state.channels[channel].synchro:
                return
        def f(L):
            return ircmsgs.quiets(channel, L)
        a = []
        d = {}
        c = self._getChan(irc,channel)
        now = time.time()
        for nick in nicks:
            m = getmask(irc,nick)
            if not m:
                continue
            if not m in d and not m in irc.state.channels[channel].quiets:
                d[m] = m
                id = self._addban(irc,channel,msg.prefix,'q',m,0)
                self._addbanaffects(irc,channel,id,'q',m)
                c.activeQuiets[str(m)] = (id,msg.prefix,'q',m,now,now)
                a.append(m)
        self._sendMsgs(irc, a, f)
    quiets = wrap(quiets, ['op', ('haveOp', 'quiet someone'),
                         any('nickInChannel')])

    def unbans(self, irc, msg, args, channel, nicks):
        """[<channel>] [<nick> ...]

        If you have the #channel,op capability, this will unban all the
        <nick>s you provide. <channel> is only necessary if the message isn't sent in the
        channel itself.
        """
        if not nicks or not channel in irc.state.channels:
            return
        else:
            if not irc.state.channels[channel].synchro:
                return
        def f(L):
            return ircmsgs.unbans(channel, L)
        a = []
        d = {}
        for nick in nicks:
            try:
                hostmask = irc.state.nickToHostmask(nick)
            except:
                continue
            mask = getmask(irc,nick)
            if not mask:
                continue
            for ban in irc.state.channels[channel].bans:
                if not ban in d:
                    if ircutils.hostmaskPatternEqual(ban,mask) or ircutils.hostmaskPatternEqual(ban,hostmask):
                        d[ban] = ban
                        a.append(ban)
                        if ban in c.activeBans:
                            (id,by,b,m,at,end) = c.activeBans[ban]
                            self._markendban(irc,channel,msg.prefix,id)
        if len(a):
            self._sendMsgs(irc, a, f)
    unbans = wrap(unbans, ['op', ('haveOp', 'unban someone'),
                         any('nickInChannel')])

    def unquiets(self, irc, msg, args, channel, nicks):
        """[<channel>] [<nick> ...]

        If you have the #channel,op capability, this will unquiet all the
        <nick>s you provide. <channel> is only necessary if the message isn't sent in the
        channel itself.
        """
        if not nicks or not channel in irc.state.channels:
            return
        else:
            if not irc.state.channels[channel].synchro:
                return
        def f(L):
            return ircmsgs.unquiets(channel, L)
        a = []
        d = {}
        for nick in nicks:
            try:
                hostmask = irc.state.nickToHostmask(nick)
            except:
                continue
            mask = getmask(irc,nick)
            if not mask:
                continue
            for quiet in irc.state.channels[channel].quiets:
                if not quiet in d:
                    if ircutils.hostmaskPatternEqual(quiet,mask) or ircutils.hostmaskPatternEqual(quiet,hostmask):
                        d[quiet] = quiet
                        if quiet in c.activeQuiets:
                            (id,by,q,m,at,end) = c.activeQuiets[quiet]
                            self._markendban(irc,channel,msg.prefix,id)
                        a.append(quiet)
                        
        if len(a):
            self._sendMsgs(irc, a, f)
    unquiets = wrap(unquiets, ['op', ('haveOp', 'unquiet someone'),
                         any('nickInChannel')])

    def kban(self, irc, msg, args, channel, text):
        """[<channel>] <nick|hostmask> [<duration>s,m,h,d,w,M,Y 0 means forever] [<reason>]"""
        if not channel in irc.state.channels:
            return
        else:
            if not irc.state.channels[channel].synchro:
                return
        c = self._getChan(irc,channel)
        now = time.time()
        bannedNick = text[0].lstrip().rstrip()
        mask = getmask(irc,bannedNick)
        if not mask:
            return
        if mask in irc.state.channels[channel].bans:
            return
        reason = msg.nick
        duration = 0
        if len(text) > 1:
            a = text[1]
            if len(text[1]) > 0:
                duration = getduration(text[1])
                if duration < 0:
                    reason = '%s - %s' % (' '.join(text),reason)
                    duration = 0
                else:
                    t = ' '.join(text)
                    t = t.replace(text[0]+' '+text[1],'').lstrip()
                    reason = '%s - %s' % (t,reason)
            else:
                duration = 0
                t = ' '.join(text)
                t = t.replace(text[0],'').lstrip()
        id = self._addban(irc,channel,msg.prefix,'b',mask,duration)
        self._addbanaffects(irc,channel,id,'b',mask)
        if duration:
            ban = (id,msg.prefix,'b',mask,now,now+duration)
            name = self._scheduleun(irc,channel,'b',mask,now+duration)
            c.activeBans[mask] = c.schedules[name] = ban
            c.pendingBans[mask] = name
        else:
            c.activeBans[mask] = (id,msg.prefix,'b',mask,now,now)
        if reason != msg.nick:
            self._banmark(irc,id,msg.prefix,reason)
        irc.sendMsg(ircmsgs.ban(channel,mask))
        if bannedNick in irc.state.channels[channel].users:
            irc.sendMsg(ircmsgs.IrcMsg('remove %s %s :%s' % (channel,bannedNick,reason)))
        if reason == msg.nick and not msg.nick == irc.nick:
            try:
                user = ircdb.users.getUser(msg.prefix)
            except KeyError:
                user = None
            if user:
                irc.queueMsg(ircmsgs.privmsg(msg.nick,"About [#%s +b %s in %s] you can use !banmark %s or !banedit %s" % (id,mask,channel,id,id)))

    kban = wrap(kban,
                ['op',
                 ('haveOp'),
                 many('something')])

    def forward(self, irc, msg, args, channel, text):
        """[<channel>] <nick|hostmask> [<duration>s,m,h,d,w,M,Y 0 means forever] <#channel>]"""
        if not channel in irc.state.channels:
            return
        else:
            if not irc.state.channels[channel].synchro:
                return
        c = self._getChan(irc,channel)
        now = time.time()
        bannedNick = text[0].lstrip().rstrip()
        mask = getmask(irc,bannedNick)
        if not mask:
            return
        if mask in irc.state.channels[channel].bans:
            return
        reason = msg.nick
        duration = 0
        if len(text) > 1:
            a = text[1]
            if len(text[1]) > 0:
                duration = getduration(text[1])
                if duration < 0:
                    reason = join(text)
                    duration = 0
                else:
                    t = ' '.join(text)
                    t = t.replace(text[0]+' '+text[1],'').lstrip()
                    reason = t
            else:
                duration = 0
                t = ' '.join(text)
                t = t.replace(text[0],'').lstrip()
        mask = '%s$%s' % (mask,reason)
        id = self._addban(irc,channel,msg.prefix,'b',mask,duration)
        self._addbanaffects(irc,channel,id,'b',mask)
        if duration:
            ban = (id,msg.prefix,'b',mask,now,now+duration)
            name = self._scheduleun(irc,channel,'b',mask,now+duration)
            c.activeBans[mask] = c.schedules[name] = ban
            c.pendingBans[mask] = name
        else:
            c.activeBans[mask] = (id,msg.prefix,'b',mask,now,now)
        if reason != msg.nick:
            self._banmark(irc,id,msg.prefix,reason)
        irc.sendMsg(ircmsgs.ban(channel,mask))
        if bannedNick in irc.state.channels[channel].users:
            irc.sendMsg(ircmsgs.IrcMsg('remove %s %s :%s' % (channel,bannedNick,msg.nick)))
        try:
            user = ircdb.users.getUser(msg.prefix)
        except KeyError:
            user = None
        if user and not msg.nick == irc.nick:
            irc.queueMsg(ircmsgs.privmsg(msg.nick,"About [#%s +b %s in %s] you can use !banmark %s or !banedit %s" % (id,mask,channel,id,id)))

    forward = wrap(forward,
                ['op',
                 ('haveOp'),
                 many('something')])

                 
    def quiet(self, irc, msg, args, channel, text):
        """[<channel>] <nick|hostmask> [<duration>s,m,h,d,w,M,Y 0 means forever] [<reason>]"""
        if not channel in irc.state.channels:
            return
        else:
            if not irc.state.channels[channel].synchro:
                return
        c = self._getChan(irc,channel)
        now = time.time()
        bannedNick = text[0].lstrip().rstrip()
        mask = getmask(irc,bannedNick)
        if not mask:
            return
        if mask in irc.state.channels[channel].quiets:
            return
        reason = msg.nick
        modes = []
        if bannedNick in irc.state.channels[channel].users:
            if bannedNick in irc.state.channels[channel].voices:
                modes.append(('-v',bannedNick))
            if bannedNick in irc.state.channels[channel].ops:
                modes.append(('-o',bannedNick))
        self.log.info('%s : %s' % (bannedNick,bannedNick in irc.state.channels[channel].voices))
        duration = 0
        if len(text) > 1:
            a = text[1]
            if len(text[1]) > 0:
                duration = getduration(text[1])
                if duration < 0:
                    reason = '%s - %s' % (' '.join(text),reason)
                    duration = 0
                else:
                    t = ' '.join(text)
                    t = t.replace(text[0]+' '+text[1],'').lstrip()
                    reason = '%s - %s' % (t,reason)
            else:
                duration = 0
                t = ' '.join(text)
                t = t.replace(text[0],'').lstrip()
        id = self._addban(irc,channel,msg.prefix,'q',mask,duration)
        self._addbanaffects(irc,channel,id,'q',mask)
        if duration:
            ban = (id,msg.prefix,'q',mask,now,now+duration)
            name = self._scheduleun(irc,channel,'q',mask,now+duration)
            c.activeQuiets[mask] = c.schedules[name] = ban
            c.pendingQuiets[mask] = name
        else:
            c.activeQuiets[mask] = (id,msg.prefix,'q',mask,now,now)
        if reason != msg.nick:
            self._banmark(irc,id,msg.prefix,reason)
        if len(modes) != 0:
            def f(L):
                return ircmsgs.modes(channel, L)
            modes.append(('+q',mask))
            self._sendMsgs(irc, modes, f)
        else:
            irc.sendMsg(ircmsgs.quiet(channel,mask))
        if reason != msg.nick and bannedNick in irc.state.channels[channel].users:
            if duration > 0:
                irc.queueMsg(ircmsgs.privmsg(bannedNick,'%s - %s' % (reason,utils.timeElapsed(duration))))
            else:
                irc.queueMsg(ircmsgs.privmsg(bannedNick,reason))
        if reason == msg.nick and not msg.nick == irc.nick:
            try:
                user = ircdb.users.getUser(msg.prefix)
            except KeyError:
                user = None
            if user:
                irc.queueMsg(ircmsgs.privmsg(msg.nick,"About [#%s +q %s in %s] you can use !banmark %s or !banedit %s" % (id,mask,channel,id,id)))
                
    quiet = wrap(quiet,
                ['op',
                 ('haveOp'),
                 many('something')])

    def _banmark (self,irc,id,nick,text):
        db = self._getbandb()
        c = db.cursor()
        try:
            c.execute("""SELECT id FROM bans WHERE id=%s""",int(id))
        except:
            return        
        if c.rowcount:
            c = db.cursor()
            try:
                c.execute("""INSERT INTO comments VALUES (%s, %s, %s, %s)""",
                        (id,nick,time.time(),text))
                db.commit()
            except:
               return        

    def unban(self, irc, msg, args, channel, text):
        """[<channel>] [<nick|hostmask|banid>]"""
        if not channel in irc.state.channels:
            return
        else:
            if not irc.state.channels[channel].synchro:
                return
        c = self._getChan(irc,channel)
        if text in irc.state.channels[channel].users:
            bans = []
            try:
                hostmask = irc.state.nickToHostmask(text)
            except:
                return
            mask = getmask(irc,text)
            if not mask:
                return
            for ban in irc.state.channels[channel].bans:
                if ircutils.hostmaskPatternEqual(ban,mask) or ircutils.hostmaskPatternEqual(ban,hostmask):
                    bans.append(ban)
                    if ban in c.activeBans:
                        (id,by,k,mask,at,end) = c.activeBans[ban]
                        self._markendban(irc,channel,msg.prefix,id)
            if len(bans):
                def f(L):
                    return ircmsgs.unbans(channel, L)
                self._sendMsgs(irc, bans, f)
        elif ircutils.isUserHostmask(text):
            bans = []
            for ban in irc.state.channels[channel].bans:
                if ircutils.hostmaskPatternEqual(ban,text):
                    bans.append(ban)
                    if ban in c.activeBans:
                        (id,by,k,mask,at,end) = c.activeBans[ban]
                        self._markendban(irc,channel,msg.prefix,id)
            if len(bans):
                def f(L):
                    return ircmsgs.unbans(channel, L)
                self._sendMsgs(irc, bans, f)
        elif text.isdigit():
            db = self._getbandb()
            c = db.cursor()
            try:
                c.execute("""SELECT channel,oper,kind,mask,begin_at,end_at,removed_at,removed_by FROM bans WHERE id=%s""",(text))
            except:
                return
            if c.rowcount:
                bans = c.fetchall()
                (channel,by,kind,mask,begin_at,end_at,removed_at,removed_by) = bans[0]
                if kind == 'b':
                    if mask in irc.state.channels[channel].bans and not removed_at:
                        self._markendban(irc,channel,msg.prefix,text)
                        irc.queueMsg(ircmsgs.unban(channel,mask))
    unban = wrap(unban, ['op',
                         ('haveOp', 'unban someone'),
                         'text'])

    def unquiet(self, irc, msg, args, channel, text):
        """[<channel>] [<nick|hostmask|banid>]"""
        if not channel in irc.state.channels:
            return
        else:
            if not irc.state.channels[channel].synchro:
                return
        c = self._getChan(irc,channel)
        text = text.lstrip()
        if text in irc.state.channels[channel].users:
            bans = []
            try:
                hostmask = irc.state.nickToHostmask(text)
            except:
                return
            mask = getmask(irc,text)
            if not mask:
                return
            for ban in irc.state.channels[channel].quiets:
                if ircutils.hostmaskPatternEqual(ban,mask) or ircutils.hostmaskPatternEqual(ban,hostmask):
                    bans.append(ban)
                    if ban in c.activeQuiets:
                        (id,by,k,mask,at,end) = c.activeQuiets[ban]
                        self._markendban(irc,channel,msg.prefix,id)
            if len(bans):
                def f(L):
                    return ircmsgs.unquiets(channel, L)
                self._sendMsgs(irc, bans, f)
        elif ircutils.isUserHostmask(text):
            bans = []
            for ban in irc.state.channels[channel].quiets:
                if ircutils.hostmaskPatternEqual(ban,text):
                    bans.append(ban)
                    if ban in c.activeQuiets:
                        (id,by,k,mask,at,end) = c.activeQuiets[ban]
                        self._markendban(irc,channel,msg.prefix,id)
            if len(bans):
                def f(L):
                    return ircmsgs.unquiets(channel, L)
                self._sendMsgs(irc, bans, f)
        elif text.isdigit():
            db = self._getbandb()
            c = db.cursor()
            try:
                c.execute("""SELECT channel,oper,kind,mask,begin_at,end_at,removed_at,removed_by FROM bans WHERE id=%s""",(text))
            except:
                return
            if c.rowcount:
                bans = c.fetchall()
                (channel,by,kind,mask,begin_at,end_at,removed_at,removed_by) = bans[0]
                if kind == 'q':
                    if mask in irc.state.channels[channel].quiets and not removed_at:
                        self._markendban(irc,channel,msg.prefix,text)
                        irc.queueMsg(ircmsgs.unquiet(channel,mask))
    unquiet = wrap(unquiet, ['op',
                         ('haveOp', 'unquiet someone'),
                         'text'])

    def invite(self, irc, msg, args, channel, nick):
        """[<channel>] <nick>

        If you have the #channel,op capability, this will invite <nick>
        to join <channel>. <channel> is only necessary if the message isn't
        sent in the channel itself.
        """
        nick = nick or msg.nick
        self._sendMsg(irc, ircmsgs.invite(nick, channel))
        self.invites[(irc.getRealIrc(), ircutils.toLower(nick))] = irc
    invite = wrap(invite, ['op', ('haveOp', 'invite someone'),
                           additional('nick')])

    def _check (self,irc,channel,ban):
        a = []
        L = []
        masks = []
        if not channel in irc.state.channels:
            return None
        for user in irc.state.channels[channel].users:
            try:
                hostmask = irc.state.nickToHostmask(user)
                masks.append([hostmask,getmask(irc,user)])
            except:
                continue
        try:
            (n,i,h) = ircutils.splitHostmask(ban)
            if h.find('$') != -1:
                h = h.split('$')[0]
                ban = '%s!%s@%s*' % (n,i,h)
        except:
            self.log.error('error in _check')
        for m in masks:
            (h,mask) = m
            if ircutils.hostmaskPatternEqual(ban,h) or ircutils.hostmaskPatternEqual(ban,mask):
               L.append(h)
        return L
        
    def check (self,irc,msg,args,channel,text):
        """[<channel>] [<banmask>] returns list of affected users"""
        L = self._check(irc,channel,text)
        if L is None:
            irc.error("i'm not in %s" % channel)
        else:
            irc.reply('%s matchs: %s' % (text,', '.join(L)))
    check = wrap(check,['op','text'])

    def restorechan (self,irc,msg,args,channel):
        """[<channel>] update channel config with supybot one"""
        self._restorechan(irc,channel)
        irc.replySuccess()
    restorechan = wrap(restorechan, ['owner','channel'])

    def baninfo (self,irc,msg,args,user,id):
        db = self._getbandb()
        c = db.cursor()
        try:
            c.execute("""SELECT channel,oper,kind,mask,begin_at,end_at,removed_at,removed_by FROM bans WHERE id=%s""",(id))
        except:
            irc.error('database is locked, try again later')
            return
        L = []
        if c.rowcount:
            bans = c.fetchall()
            (channel,by,kind,mask,begin_at,end_at,removed_at,removed_by) = bans[0]
            on = time.strftime('%Y-%m-%d %H:%M:%S GMT',time.gmtime(float(begin_at)))
            L.append('%s in %s, %s sets +%s %s' % (on,channel,by,kind,mask))
            was = float(begin_at) == float(end_at)
            if was:
                was = 'forever'
            else:
                was = utils.timeElapsed(float(end_at) - float(begin_at))
            if not removed_at:
                if was == 'forever':
                    L.append('duration is %s' % was)
                else:
                    L.append('duration is %s and will expire in %s' % (was,utils.timeElapsed(float(end_at) - time.time())))
            else:
                L.append('original duration was %s' % was)
                L.append('removed after %s on %s by %s' % (utils.timeElapsed(float(removed_at)-float(begin_at)),time.strftime('%Y-%m-%d %H:%M:%S GMT',time.gmtime(float(removed_at))),removed_by))
            c = db.cursor()
            try:
                c.execute("""SELECT oper, comment FROM comments WHERE ban_id=%s""",(id))
                if c.rowcount:
                    comments = c.fetchall()
                    for comment in comments:
                        (n,t) = comment
                        L.append('comment by %s: %s' % (n,t))
                else:
                    L.append('no comment can be found')
            except:
                L.append('error when trying to read comment')
                
            c = db.cursor()
            try:
                c.execute("""SELECT full FROM nicks WHERE ban_id=%s""",(id))
                if c.rowcount:
                    bans = c.fetchall()
                    for ban in bans:
                        L.append('affected %s' % ban[0])
                    L.append('See !banlog %s for log' % id)
            except:
                L.append('error when trying to read affected users')
        for line in L:
            irc.queueMsg(ircmsgs.privmsg(msg.nick,line))
            
    baninfo = wrap(baninfo, ['user', 'nonNegativeInt'])

    def bansearch (self,irc,msg,args,user,text):
        """[<hostmask|nick>] returns bans match"""
        db = self._getbandb()
        t = '*%s*' % text
        c = db.cursor()
        try:
            c.execute("""SELECT ban_id,full FROM nicks WHERE full GLOB %s ORDER BY ban_id DESC""",(t))
        except:
            irc.reply('database locked, try again later')
            return
        L = []
        a = {} 
        if c.rowcount:
            bans = c.fetchall()
            d = {}
            for ban in bans:
                (id,full) = ban
                if not id in d:
                    d[id] = id
            for ban in d:
                c = db.cursor()
                try:
                    c.execute("""SELECT id, mask, kind, channel FROM bans WHERE id=%s ORDER BY id DESC""",(int(ban)))
                    if c.rowcount:
                        bans = c.fetchall()
                        for ban in bans:
                            (id,mask,kind,channel) = ban
                            a[id] = ban
                except:
                    irc.reply('database locked, try again later')
                    return
        c = db.cursor()
        try:
            c.execute("""SELECT id, mask, kind, channel FROM bans WHERE mask GLOB %s ORDER BY id DESC""",(t))
            if c.rowcount:
                   bans = c.fetchall()
                   for ban in bans:
                        (id,mask,kind,channel) = ban
                        a[id] = ban
        except:
            irc.reply('database locked, try again later')
            return
        if len(a):
            ar = []
            for ban in a:
                (id,mask,kind,channel) = a[ban]
                ar.append([int(id),mask,kind,channel])
            ar.sort(reverse=True)
            i = 0
            while i < len(ar):
                (id,mask,kind,channel) = ar[i]
                L.append('[#%s +%s %s in %s]' % (id,kind,mask,channel))
                i = i+1
            irc.reply(', '.join(L))
        else:
            irc.reply('no ban found')
    bansearch = wrap(bansearch, ['user','text'])
    
    def banedit(self, irc, msg, args, user, id, text):
        """[<id>] [<duration>s,m,h,d,w,M,Y 0 means forever] change duration of an active ban/quiet"""
        db = self._getbandb()
        c = db.cursor()
        try:
            c.execute("""SELECT channel, oper, kind, mask, begin_at, end_at, removed_at FROM bans WHERE id=%s""",int(id))
        except:
            irc.error('database locked, try again later')
            return        
        if c.rowcount:
            bans = c.fetchall()
            (channel,by,kind,mask,begin_at,end_at,removed_at) = bans[0]
            if removed_at:
                irc.error('this ban/quiet has been removed')
                return
            was = float(end_at) - float(begin_at)
            if channel in irc.state.channels:
                chan = self._getChan(irc,channel)
                if not irc.state.channels[channel].synchro or not chan.synchro:
                    irc.error('please, try again later, channel is not synchronised yet.')
                    return
                if kind == 'q':
                    if not mask in irc.state.channels[channel].quiets:
                        irc.reply('there is no +q %s in %s' % (mask,channel))
                        return
                elif kind == 'b':
                    if not mask in irc.state.channels[channel].bans:
                        irc.reply('there is no +b %s in %s' % (mask,channel))
                        return
            d = getduration(text)
            t = time.time()
            if d < 1:
                irc.queueMsg(ircmsgs.IrcMsg('MODE %s -%s %s' % (channel,kind,mask)))
                return
            t = t+d
            c = db.cursor()
            ban = (id,by,kind,mask,begin_at,t)
            try:
                c.execute("""UPDATE bans SET end_at=%s WHERE id=%s""",(t,id))
            except:
                irc.error('database locked, try again later')
                return
            db.commit()
            if kind == 'b':
                if mask in chan.pendingBans:
                    name = chan.pendingBans[mask]
                    try:
                        schedule.removeEvent(name)
                    except:
                        self.log.info('cannot found schedule %s : %s %s' % (name,mask,channel))
                else:
                    self.log.info('no schedule %s %s' % (mask,channel))
                    name = self._scheduleun(irc,channel,kind,mask,t)
                    chan.activeBans[mask] = chan.schedules[name] = ban
                    chan.pendingBans[mask] = name
            elif kind == 'q':
                if mask in chan.pendingQuiets:
                    name = chan.pendingQuiets[mask]
                    try:
                        schedule.removeEvent(name)
                    except:
                        self.log.error('cannot remove schedule %s : %s %s' % (name,mask,channel))
                else:
                    self.log.info('no schedule %s %s' % (mask,channel))
                name = self._scheduleun(irc,channel,kind,mask,t)
                chan.activeQuiets[mask] = chan.schedules[name] = ban
                chan.pendingQuiets[mask] = name
            if was == 0:
                was = 'forever'
            else:
                was = utils.timeElapsed(was)+' with %s remaining' % utils.timeElapsed(float(end_at) - time.time())
            self._banmark(irc,id,msg.prefix,'duration updated : %s ( was %s )' % (text,was))
            irc.reply('#%s duration updated for +%s %s in %s (was %s)' % (id,kind,mask,channel,was))
        else:
            irc.reply('there is no ban/quiet #%s' % id)
    banedit = wrap(banedit, ['user', 'nonNegativeInt','text'])

    def banmark(self,irc,msg,args,user,id,text):
        """[<id>] [<text>] comment a ban/quiet"""
        db = self._getbandb()
        c = db.cursor()
        try:
            c.execute("""SELECT id FROM bans WHERE id=%s""",int(id))
        except:
            irc.error('database locked, try again later')
            return        
        if c.rowcount:
            c = db.cursor()
            try:
                c.execute("""INSERT INTO comments VALUES (%s, %s, %s, %s)""",
                        (id,msg.prefix,time.time(),text))
                db.commit()
            except:
               irc.reply('database locked, try again later') 
               return
            irc.queueMsg(ircmsgs.privmsg(msg.nick,'done. see !baninfo %s or !banlog %s or !banaffects %s' % (id,id,id)))
        else:
            irc.reply('there is no ban/quiet with id #%s' % id)
    banmark = wrap(banmark, ['user', 'nonNegativeInt','text'])
    
    def banaffects(self, irc, msg, args, user, id):
        """[<id>] return list of users affected by a ban/quiet"""
        db = self._getbandb()
        c = db.cursor()
        try:
            c.execute("""SELECT channel, oper, kind, mask, begin_at FROM bans WHERE id=%s LIMIT 1""",(id))
        except:
            irc.error('database locked, try again later')
            return
        s = ''
        if c.rowcount:
            bans = c.fetchall()
            (channel,by,kind,mask,begin_at) = bans[0]
            s += 'On %s, by %s in %s +%s %s' % (time.strftime('%Y-%m-%d %H:%M:%S GMT',time.gmtime(float(begin_at))),by,channel,kind,mask)
        else:
            irc.reply('there is no ban/quiet with #%s' % id)
            return
        c = db.cursor()
        L = []
        try:
            c.execute("""SELECT full FROM nicks WHERE ban_id=%s""",(id))
        except:
            irc.error('database locked, try again later')
            return
        if c.rowcount:
            bans = c.fetchall()
            for ban in bans:
                L.append('%s' % ban[0])
            s += ' affected %s user(s): %s' % (len(L),', '.join(L))
            irc.reply(s)
        else:
            s += ' affected 0 user'
            irc.reply(s)
    banaffects = wrap(banaffects, ['user', 'nonNegativeInt'])
    
    def banlog (self,irc,msg,args,user,id):
        """[<id>] return log of affected users by a ban"""
        db = self._getbandb()
        c = db.cursor()
        try:
            c.execute("""SELECT id FROM bans WHERE id=%s LIMIT 1""",(id))
        except:
            irc.error('database locked, try again later')
            return
        if not c.rowcount:
            irc.reply('there is no ban/quiet with #%s' % id)
            return
        L = []
        L.append('Logs of #%s:' % id)
        c = db.cursor()
        try:
            c.execute("""SELECT full, log FROM nicks WHERE ban_id=%s""",(id))
        except:
            irc.error('database locked, try again later')
            return
        if c.rowcount:
            users = c.fetchall()
            for u in users:
                (full,log) = u
                L.append('for %s' % full)
                if log != '':
                    for line in log.split('\n'):
                        if line:
                            L.append(line)
                L.append('--')
            for line in L:
                irc.queueMsg(ircmsgs.privmsg(msg.nick,line))
        else:
            irc.reply('no log for this ban/quiet')
    banlog = wrap(banlog, ['user', 'nonNegativeInt'])

    def makeDb(self, filename):
        if os.path.exists(filename):
            return sqlite.connect(filename)
        db = sqlite.connect(filename)
        c = db.cursor()
        c.execute("""CREATE TABLE datas (
                id INTEGER PRIMARY KEY,
                mask VARCHAR(500) NOT NULL,
                time TIMESTAMP NOT NULL,
                regexp TEXT NOT NULL,
                action TEXT NOT NULL,
                kind VARCHAR(4)
                )""")
        db.commit()
        return db

    def _restoreregexps(self,channel):
        db = self.getDb(channel)
        c = db.cursor()
        c.execute("""SELECT id, regexp, action, kind FROM datas WHERE 1=1""")
        L = []
        for item in c.fetchall():
            L.append([item[0],item[1],item[2],item[3],utils.str.perlReToPythonRe(item[1])])
        self.log.info('%s regexps restored for %s' % (len(L),channel))
        return L

    def onjoin (self,irc,msg,args,channel,text):
        """[channel] </regexp/ @ action> triggered at each join"""
        db = self.getDb(channel)
        cu = db.cursor()
        mask = irc.state.nickToHostmask(msg.nick)
        a = text.split('@')
        c = self._getChan(irc,channel)
        if len(a) == 2:
            regexp = a[0].rstrip()
            action = a[1].lstrip()
            try:
                reg = utils.str.perlReToPythonRe(regexp)
            except:
                irc.reply('bad regular expression, see : http://www.cs.tut.fi/~jkorpela/perl/regexp.html')
                return
            cu.execute("""INSERT INTO datas VALUES (NULL, %s, %s, %s, %s, 'JOIN')""", mask, int(time.time()), regexp, action)
            id = db.insert_id()
            db.commit()
            c.regexps.append([id,regexp,action,'JOIN',reg]) 
            irc.reply('#%s done' % id)
        else:
            irc.reply('usage: /regexp/ @ command')
    onjoin = wrap(onjoin,['channel','op','text'])

    def onnick (self,irc,msg,args,channel,text):
        """[channel] </regexp/ @ action> trigger when user change nick ( you can use $nick, $channel, $mask, $text )"""
        db = self.getDb(channel)
        cu = db.cursor()
        mask = irc.state.nickToHostmask(msg.nick)
        a = text.split('@')
        c = self._getChan(irc,channel)
        if len(a) == 2:
            regexp = a[0].rstrip()
            action = a[1].lstrip()
            try:
                reg = utils.str.perlReToPythonRe(regexp)
            except:
                irc.reply('bad regular expression, see : http://www.cs.tut.fi/~jkorpela/perl/regexp.html')
                return
            cu.execute("""INSERT INTO datas VALUES (NULL, %s, %s, %s, %s, 'NICK')""", mask, int(time.time()), regexp, action)
            id = db.insert_id()
            db.commit()
            c.regexps.append([id,regexp,action,'NICK',reg]) 
            irc.reply('#%s done' % id)
        else:
            irc.reply('usage: /regexp/ @ command')
    onnick = wrap(onnick, ['channel','op','text'])
    
    def watch (self,irc,msg,args,channel,text):
        """[channel] <nick|/regexp/> forward message to logChannel"""
        c = self._getChan(irc,channel)
        if not c.logChannel in irc.state.channels:
            irc.reply("There is no logChannel setted for %s or i'm not in" % channel)
            return
        regexp = ''
        if text in irc.state.channels[channel].users:
            reg = getmask(irc,text)
            reg = reg.replace('~','')
            reg = reg.replace('/','\/')
            reg = reg.replace('.','\.')
            reg = reg.replace('*','\*')
            reg = reg.replace('[','\[')
            reg = reg.replace(']','\]')
            regexp = '/%s/' % reg
        else:
            regexp = text
        self.log.info('trying to watch %s in %s' % (regexp,channel))        
        db = self.getDb(channel)
        cu = db.cursor()
        mask = irc.state.nickToHostmask(msg.nick)
        action = ''
        try:
            reg = utils.str.perlReToPythonRe(regexp)
        except:
            irc.reply('Sorry, bad regexp, or cannot found user, take a look here : http://www.cs.tut.fi/~jkorpela/perl/regexp.html')
            return
        cu.execute("""INSERT INTO datas VALUES (NULL, %s, %s, %s, %s, 'TEXT')""", mask, int(time.time()), regexp, action)
        id = db.insert_id()
        action = 'channel private %s [$channel](#%s) <$nick> $text' % (c.logChannel,id)
        cu.execute("""UPDATE datas SET action=%s WHERE id=%s""", (action,int(id)))
        db.commit()
        c.regexps.append([id,regexp,action,'TEXT',reg]) 
        irc.reply('#%s done' % id)  
    watch = wrap(watch, ['channel','op','text'])

    def unwatch (self,irc,msg,args,channel,text):
        """[channel] <nick|/regexp/> delete a forward to logChannel trigger"""
        c = self._getChan(irc,channel)
        if not c.logChannel in irc.state.channels:
            irc.reply("i'm not in %s" % c.logChannel)
            return
        regexp = ''
        if text in irc.state.channels[channel].users:
            reg = getmask(irc,text)
            reg = reg.replace('~','')
            reg = reg.replace('/','\/')
            reg = reg.replace('.','\.')
            reg = reg.replace('*','\*')
            reg = reg.replace('[','\[')
            reg = reg.replace(']','\]')
            regexp = '/%s/' % reg
        else:
            regexp = text
        db = self.getDb(channel)
        cu = db.cursor()
        cu.execute("""SELECT id FROM datas WHERE regexp='%s'""" % regexp)
        if cu.rowcount == 0:    
            irc.reply('sorry, no such regexp')
            return
        id = int(cu.fetchall()[0][0])
        cu.execute("""DELETE FROM datas WHERE id=%s""" % id)
        db.commit()
        for item in c.regexps:
            if item[0] == id:
                index = c.regexps.index(item)
                del c.regexps[index]
                irc.reply('#%s deleted' % id)   
                return
        irc.reply("can't found %s in regular expression database of %s" % (regexp,channel))
    unwatch = wrap(unwatch, ['channel','op','text'])

    def warn (self,irc,msg,args,channel,text):
        """[channel] </regexp/ @ number action> trigger at each message in channel ( you can use $nick, $channel, $mask, $text )"""
        c = self._getChan(irc,channel)
        db = self.getDb(channel)
        cu = db.cursor()
        mask = irc.state.nickToHostmask(msg.nick)
        a = text.split('@')
        if len(a) == 2:
            regexp = a[0].rstrip()
            action = a[1].lstrip()
            try:
                reg = utils.str.perlReToPythonRe(regexp)
            except:
                irc.reply('Sorry, bad regexp, take a look here : http://www.cs.tut.fi/~jkorpela/perl/regexp.html')
                return
            cu.execute("""INSERT INTO datas VALUES (NULL, %s, %s, %s, %s, 'WARN')""", mask, int(time.time()), regexp, action)
            id = db.insert_id()
            db.commit()
            c.regexps.append([id,regexp,action,'WARN',reg])
            irc.reply('#%s done' % id)
        else:
            irc.reply('usage: /regexp/ @ command')
    warn = wrap(warn,['channel','op','text'])
    
    def regadd (self,irc,msg,args,channel,text):
        """[channel] </regexp/ @ action> trigger at each message in channel ( you can use $nick, $channel, $mask, $text )"""
        c = self._getChan(irc,channel)
        db = self.getDb(channel)
        cu = db.cursor()
        mask = irc.state.nickToHostmask(msg.nick)
        a = text.split('@')
        if len(a) == 2:
            regexp = a[0].rstrip()
            action = a[1].lstrip()
            try:
                reg = utils.str.perlReToPythonRe(regexp)
            except:
                if msg.nick != irc.nick:
                    irc.reply('Sorry, bad regexp, take a look here : http://www.cs.tut.fi/~jkorpela/perl/regexp.html')
                return
            cu.execute("""INSERT INTO datas VALUES (NULL, %s, %s, %s, %s, 'TEXT')""", mask, int(time.time()), regexp, action)
            id = db.insert_id()
            db.commit()
            c.regexps.append([id,regexp,action,'TEXT',reg]) 
            if msg.nick != irc.nick:
                irc.reply('#%s done' % id)
        else:
            if msg.nick != irc.nick:
                irc.reply('usage: /regexp/ @ command')
    regadd = wrap(regadd,['channel','op','text'])

    def reglist (self,irc,msg,args,channel):
        """[channel] list regexp in database"""
        c = self._getChan(irc,channel)
        L = []
        for item in c.regexps:
             L.append('[#%s %s @ %s |%s]' % (item[0],item[1],item[2],item[3]))
        if len(L) != 0:
            irc.reply(', '.join(L))
        else:
            irc.reply('no regexp on %s' % channel)
    reglist = wrap(reglist,['channel','op'])    

    def reginfo (self,irc,msg,args,channel,text):
        """[channel] <id> give information about a regexp"""
        id = -1
        if text.isdigit():
            id = int(text)
            db = self.getDb(channel)
            c = db.cursor()
            c.execute("""SELECT mask, regexp, action, time, kind  FROM datas WHERE id LIKE %s""" %id)
            if c.rowcount == 0:
                irc.reply('no such id')
                return
            matchs = c.fetchall()
            (mask,reg,action,at,kind) = matchs[0]
            at = time.strftime(conf.supybot.reply.format.time(),time.localtime(int(at)))
            irc.queueMsg(ircmsgs.privmsg(msg.nick,'%s: #%i %s @ %s by %s at %s / %s' % (msg.nick, id, reg, action, mask, at, kind)))
        else:
            irc.reply('%s is not an id' % text)
    reginfo = wrap(reginfo,['channel','op','text'])

    def regdel (self,irc,msg,args,channel,text):
        """[channel] <id|/regexp/> delete regexp by id or regexp"""
        id = -1
        c = self._getChan(irc,channel)
        db = self.getDb(channel)
        if text.isdigit():
            id = int(text)
            cu = db.cursor()
            cu.execute("""SELECT id FROM datas WHERE id LIKE %s""" %id)
            if cu.rowcount == 0:    
                irc.error('sorry, no such id')
                return
            else:
                cu.execute("""DELETE FROM datas WHERE id LIKE %s""" % id)
                db.commit()
                for item in c.regexps:
                    if item[0] == id:
                        index = c.regexps.index(item)
                        del c.regexps[index]
                        irc.reply('#%s deleted' % id)
                        return
        else:
            cu = db.cursor()
            cu.execute("""SELECT id FROM datas WHERE regexp='%s'""" % text)
            if cu.rowcount == 0:
                if msg.nick != irc.nick:    
                    irc.reply('sorry, no such regexp')
                return
            id = int(cu.fetchall()[0][0])
            cu.execute("""DELETE FROM datas WHERE id=%s""" % id)
            db.commit()
            for item in c.regexps:
                if item[0] == id:
                    index = c.regexps.index(item)
                    del c.regexps[index]
                    if msg.nick != irc.nick:
                        irc.reply('#%s deleted' % id)   
                    return
        if msg.nick != irc.nick:
            irc.reply('sorry, no such regexp')
    regdel = wrap(regdel,['channel','op','text'])

    def _restorechan (self,irc,channel):
        self._delChan(irc,channel)
        self._getChan(irc,channel)
        

    def _getChan (self,irc,channel):
        ch = channel
        channel = channel.lower()
        if not irc in self.ircs:
            self.ircs[irc] = {}
        if not channel in self.ircs[irc]:
            c = self.ircs[irc][channel] = Chan ()
            c.logChannel = self.registryValue('logChannel',channel=ch)
            c.logSize = self.registryValue('logSize',channel=ch)
            c.opChannel = self.registryValue('opChannel',channel=ch)
            
            c.evadeBanCheck = self.registryValue('evadeBanCheck',channel=ch)
            c.evadeKickMessage = self.registryValue('evadeKickMessage',channel=ch)
            c.evadeBanDuration = self.registryValue('evadeBanDuration',channel=ch)
            
            c.floodCheck = self.registryValue('floodCheck',channel=ch)
            c.floodPermit = self.registryValue('floodPermit',channel=ch)
            c.floodLife = self.registryValue('floodPermit',channel=ch)
            c.floodQuietDuration = self.registryValue('floodQuietDuration',channel=ch)
            if c.floodCheck:
                c.floodQueue = SpamQueue (c.floodLife)
            c.lowFloodCheck = self.registryValue('lowFloodCheck',channel=ch)
            c.lowFloodPermit = self.registryValue('lowFloodPermit',channel=ch)
            c.lowFloodLife = self.registryValue('lowFloodLife',channel=ch)
            c.lowFloodQuietDuration = self.registryValue('lowFloodQuietDuration',channel=ch)
            if c.lowFloodCheck:
                c.lowFloodQueue = SpamQueue(c.lowFloodLife)
            c.floodMessage = self.registryValue('floodMessage',channel=ch)
            
            c.repeatCheck = self.registryValue('repeatCheck',channel=ch)
            c.repeatPermit = self.registryValue('repeatPermit',channel=ch)
            c.repeatLife = self.registryValue('repeatLife',channel=ch)
            c.repeatMessage = self.registryValue('repeatMessage',channel=ch)
            c.repeatQuietDuration = self.registryValue('repeatQuietDuration',channel=ch)
            if c.repeatCheck:
                c.repeatQueue = SpamQueue (c.repeatLife)
            
            c.highlightCheck = self.registryValue('highlightCheck',channel=ch)
            c.highlightPermit = self.registryValue('highlightPermit',channel=ch)
            c.highlightMessage = self.registryValue('highlightMessage',channel=ch)
            c.highlightQuietDuration = self.registryValue('highlightQuietDuration',channel=ch)
            
            c.noticeCheck = self.registryValue('noticeCheck',channel=ch)
            c.noticeLife = self.registryValue('noticeLife',channel=ch)
            c.noticePermit = self.registryValue('noticePermit',channel=ch)
            c.noticeMessage = self.registryValue('noticeMessage',channel=ch)
            c.noticeQuietDuration = self.registryValue('noticeQuietDuration',channel=ch)
            
            if c.noticeCheck:
                c.noticeQueue = SpamQueue(c.noticeLife)

            c.badUserLife = self.registryValue('badUserLife',channel=ch)
            c.badUserQueue = SpamQueue(c.badUserLife)
            c.badUserPermit = self.registryValue('badUserPermit',channel=ch)
            c.badUserMessage = self.registryValue('badUserMessage',channel=ch)
            c.badUserBanDuration = self.registryValue('badUserBanDuration',channel=ch)
            
            c.massjoinCheck = self.registryValue('massjoinCheck',channel=ch)
            c.massjoinLife = self.registryValue('massjoinLife',channel=ch)
            if c.massjoinCheck:
                c.massjoinQueue = SpamQueue(c.massjoinLife)
            c.massjoinPermit = self.registryValue('massjoinPermit',channel=ch)
            c.massjoinMode = self.registryValue('massjoinMode',channel=ch)
            c.massjoinUnMode = self.registryValue('massjoinUnMode',channel=ch)
            c.massjoinDuration = self.registryValue('massjoinDuration',channel=ch)

            c.cycleCheck = self.registryValue('cycleCheck',channel=ch)
            c.cycleLife = self.registryValue('cycleLife',channel=ch)
            if c.cycleCheck:
                c.cycleQueue = SpamQueue(c.cycleLife)
            c.cyclePermit = self.registryValue('cyclePermit',channel=ch)
            c.cycleBanDuration = self.registryValue('cycleBanDuration',channel=ch)
            
            c.regexps = self._restoreregexps(ch)
            c.commandCheck = self.registryValue('commandCheck',channel=ch)
            c.commandPermit = self.registryValue('commandPermit',channel=ch)
            c.commandLife = self.registryValue('commandLife',channel=ch)
            if c.commandCheck:
                c.commandQueue = SpamQueue(c.commandLife)
            c.commandDisableDuration = self.registryValue('commandDisableDuration',channel=ch)
            c.warnLife = self.registryValue('warnLife',channel=ch)
            if not c.synchro and channel in irc.state.channels and irc.state.channels[channel].synchro:
                self._syncChan(irc,channel)
        else:
            c = self.ircs[irc][channel]
            if not c.synchro and channel in irc.state.channels and irc.state.channels[channel].synchro:
                self._syncChan(irc,channel)
        return c
    
    def _delChan (self,irc,channel):
        channel = channel.lower()
        if not irc in self.ircs:
            return
        if not channel in self.ircs[irc]:
            return
        c = self.ircs[irc][channel]
        if c.synchro:
            if c.warnSchedule:
                try:
                    schedule.removeEvent(c.warnSchedule)
                except:
                    self.log.error('cannot remove %s' % c.warnSchedule)
            if len(c.schedules):
                for s in c.schedules:
                    try:
                        schedule.removeEvent(s)
                    except:
                        continue
        del self.ircs[irc][channel]
        self.log.info('%s removed' % channel)
    
    def _syncChan (self,irc,channel):
        c = self.ircs[irc][channel]
        db = self._getbandb()
        cu = db.cursor()
        cu.execute("""SELECT id, oper, kind, mask, begin_at, end_at, removed_at FROM bans WHERE channel=%s ORDER BY id""",channel)
        toForget = []
        toRemove = []
        if cu.rowcount:
            bans = cu.fetchall()
            t = time.time()
            for fullban in bans:
                (id,by,kind,mask,begin,end,removed_at) = fullban
                ban = (id,by,kind,mask,begin,end)
                if not removed_at:
                    #self.log.info('%s %s %s' % (kind,mask,float(begin) != float(end)))
                    if float(begin) != float(end):
                        active = float(end) > t
                        if active:
                            if kind == 'b':
                                if mask in irc.state.channels[channel].bans:
                                    name = self._scheduleun(irc,channel,kind,mask,end)
                                    c.activeBans[mask] = c.schedules[name] = ban 
                                    c.pendingBans[mask] = name
                                else:
                                    toForget.append(ban)
                            elif kind == 'q':
                                if mask in irc.state.channels[channel].quiets:
                                    name = self._scheduleun(irc,channel,kind,mask,end)
                                    c.activeQuiets[mask] = c.schedules[name] = ban
                                    c.pendingQuiets[mask] = name
                                else:
                                    toForget.append(ban)
                        else:
                            if kind == 'b':
                                if mask in irc.state.channels[channel].bans:
                                    c.activeBans[mask] = ban
                                    toRemove.append(ban)
                                else:
                                    toForget.append(ban)
                            elif kind == 'q':
                                if mask in irc.state.channels[channel].quiets:
                                    c.activeQuiets[mask] = ban
                                    toRemove.append(ban)
                                else:
                                    toForget.append(ban)
                    else:
                        if kind == 'b':
                            if mask in irc.state.channels[channel].bans:
                                c.activeBans[mask] = ban
                            else:
                                toForget.append(ban)
                        elif kind == 'q':
                            if mask in irc.state.channels[channel].quiets:
                                c.activeQuiets[mask] = ban
                            else:
                                toForget.append(ban)            
            m = []
            # bans/quiets to remove now as they expired
            for ban in toRemove:
                (id,by,kind,mask,begin,end) = ban
                m.append(('-%s' % kind,mask))
            def f(L):
                return ircmsgs.modes(channel,L)
            self._sendMsgs(irc,m,f)
            # bans/quiets removed when bot was offline
            for ban in toForget:
                (id,by,kind,mask,begin,end) = ban
                self._endban (irc,channel,'Unknow!~unknow@unknow',id)
        now = time.time()
        # new bans during offline
        for b in irc.state.channels[channel].bans:
            if not b in c.activeBans and b in irc.state.channels[channel].bansOwner:
                id = self._addban(irc,channel,irc.state.channels[channel].bansOwner[b],'b',b,0)
                #self._addbanaffects(irc,channel,id,'b',b)
                c.activeBans[str(b)] = (id,irc.state.channels[channel].bansOwner[b],'b',b,now,now)
        # new quiets during offline
        for q in irc.state.channels[channel].quiets:
            if not q in c.activeQuiets and q in irc.state.channels[channel].quietsOwner:
                id = self._addban(irc,channel,irc.state.channels[channel].quietsOwner[q],'q',q,0)
                #self._addbanaffects(irc,channel,id,'q',b)
                c.activeQuiets[str(q)] = (id,irc.state.channels[channel].quietsOwner[q],'q',q,now,now)
        
        def clearWarn():
            for u in c.nicks:
                c.nicks[u].warns = 0
        c.warnSchedule = schedule.addEvent(clearWarn,c.warnLife)
        #self.log.info('There is %s bans and %s quiets in %s, %s scheduled' % (len(irc.state.channels[channel].bans),len(irc.state.channels[channel].quiets),channel,len(c.schedules)))
#        if c.logChannel in irc.state.channels:
            #irc.queueMsg(ircmsgs.privmsg(c.logChannel,'[%s] remove %s q/b' % (channel,len(toRemove))))
            #irc.queueMsg(ircmsgs.privmsg(c.logChannel,'[%s] forget %s q/b' % (channel,len(toForget))))
            #irc.queueMsg(ircmsgs.privmsg(c.logChannel,'[%s] schedule %s q/b' % (channel,len(c.schedules))))
            #irc.queueMsg(ircmsgs.privmsg(c.logChannel,'[%s] active %s q/b' % (channel,(len(c.activeQuiets)+len(c.activeBans)))))
            #irc.queueMsg(ircmsgs.privmsg(c.logChannel,'[%s] %s regulars expressions restored' % (channel,len(c.regexps))))
            #irc.queueMsg(ircmsgs.privmsg(c.logChannel,'[%s] flood %s, lowFlood %s, evadeBan %s, repeat %s, highlight %s, notice/ctcp %s, massjoin %s, cycle %s' % (channel,c.floodCheck,c.lowFloodCheck,c.evadeBanCheck,c.repeatCheck,c.highlightCheck,c.noticeCheck,c.massjoinCheck,c.cycleCheck)))
            #irc.queueMsg(ircmsgs.privmsg(c.logChannel,'[%s] synchronised' % channel))

        self.log.info('** %s synchronised' % channel)
        c.synchro = True
    
    def _scheduleun(self,irc,channel,kind,mask,end):
        def un():
            if channel in irc.state.channels and irc.state.channels[channel].synchro and irc.nick in irc.state.channels[channel].ops:
                c = self._getChan(irc,channel)
                if kind == 'b' and \
                    mask in irc.state.channels[channel].bans:
                    irc.queueMsg(ircmsgs.IrcMsg('MODE %s -b %s' % (channel,mask)))
                elif kind == 'q' and \
                    mask in irc.state.channels[channel].quiets:
                    irc.queueMsg(ircmsgs.IrcMsg('MODE %s -q %s' % (channel,mask)))
                else:
                    self.log.info('error, nothing to do for -%s in %s about %s' % (kind,channel,mask))
        return schedule.addEvent(un,float(end))

    def die(self):
        for irc in self.ircs:
            for channel in self.ircs[irc]:
                c = self._getChan(irc,channel)
                if len(c.schedules):
                    for s in c.schedules:
                        try:
                            schedule.removeEvent(s)
                        except:
                            continue
        self.ircs = {}
        
            
    def _doLog(self,irc,channel,nick,message):
        c = self._getChan(irc,channel)
        try:
            hostmask = nick
            (n, u, h) = ircutils.splitHostmask(hostmask)
            m = ircutils.joinHostmask('*',u,h)
        except:
            return
        if not n in c.nicks:
            oNick = Nick ()
            oNick.nick = n
            oNick.host = hostmask
            oNick.mask = getmask(irc,n)
            c.nicks[n] = oNick
        else:
            oNick = c.nicks[n]
        if len(oNick.logs) > c.logSize:
            oNick.logs.dequeue()
        oNick.logs.enqueue('%s %s' % (time.strftime('%Y-%m-%d %H:%M:%S GMT',time.gmtime()),message))
    
    def _getUser (self,irc,channel,nick):
        c = self._getChan(irc,channel)
        try:
            hostmask = nick
            (n, u, h) = ircutils.splitHostmask(hostmask)
            m = ircutils.joinHostmask('*',u,h)
        except:
            return
        if not n in c.nicks:
            oNick = Nick ()
            oNick.nick = n
            oNick.host = hostmask
            oNick.mask = getmask(irc,n)
            c.nicks[n] = oNick
        else:
            oNick = c.nicks[n]
        return oNick

    def makeDb(self, filename):
        if os.path.exists(filename):
            return sqlite.connect(filename)
        db = sqlite.connect(filename)
        c = db.cursor()
        c.execute("""CREATE TABLE datas (
                id INTEGER PRIMARY KEY,
                mask VARCHAR(1000) NOT NULL,
                time TIMESTAMP NOT NULL,
                regexp TEXT NOT NULL,
                action TEXT NOT NULL,
                kind VARCHAR(4) NOT NULL
                )""")
        db.commit()
        return db

    def _getbandb (self):
        filename = self.registryValue('banDatabase')
        if os.path.exists(filename):
            return sqlite.connect(filename)
        db = sqlite.connect(filename)
        c = db.cursor()
        c.execute("""CREATE TABLE bans (
                id INTEGER PRIMARY KEY,
                channel VARCHAR(100) NOT NULL,
                oper VARCHAR(1000) NOT NULL,
                kind VARCHAR(1) NOT NULL,
                mask VARCHAR(1000) NOT NULL,
                begin_at TIMESTAMP NOT NULL,
                end_at TIMESTAMP NOT NULL,
                removed_at TIMESTAMP,
                removed_by VARCHAR(1000)
                )""")
        c.execute("""CREATE TABLE nicks (
                ban_id INTEGER,
                ban VARCHAR(1000) NOT NULL,
                full VARCHAR(1000) NOT NULL,
                log TEXT NOT NULL
                )""")
        c.execute("""CREATE TABLE comments (
                ban_id INTEGER,
                oper VARCHAR(1000) NOT NULL,    
                at TIMESTAMP NOT NULL,
                comment TEXT NOT NULL
                )""")
        db.commit()
        return db
    
    def _addbanaffects (self,irc,channel,id,kind,ban):
        chan = self._getChan(irc,channel)
        db = self._getbandb()
        L = self._check(irc,channel,ban)
        if not L or len(L) == 0:
            L = []
            for n in chan.nicks:
                if ircutils.hostmaskPatternEqual(ban,chan.nicks[n].host):
                    L.append(chan.nicks[n].host)
        if L and len(L):
            for u in L:
                c = db.cursor()
                n = u.split('!')[0]
                log = ''
                if n in chan.nicks:
                    count = 0
                    for line in chan.nicks[n].logs:
                        log += chan.nicks[n].logs[count]+'\n'
                        count += 1
                try:
                    c.execute("""INSERT INTO nicks VALUES (%s, %s, %s, %s)""",(id,ban,u,log))
                    db.commit()
                except:
                    self.log.info('error in _addbanaffects with %s %s %s %s' % (channel,id,kind,ban))
                    continue
                    
    def _addban (self,irc,channel,oper,kind,ban,duration):
        db = self._getbandb()
        channel = channel.lower()
        c = db.cursor()
        now = time.time()
        if not duration < 1:
            end = now+duration
        else:
            end = now
        try:
            c.execute("""INSERT INTO bans VALUES (NULL, %s, %s, %s, %s, %s, %s, NULL, NULL)""",
                (channel,oper,kind,ban,now,end))
        except:
            return
        db.commit()
        id = int(db.insert_id())
        return id
    
    def _summaryBan(self,irc,channel,by,id,kind,mode,mask,begin,end,remove):
        s = '[#%s %s%s %s by %s' % (id,mode,kind,mask,by.split('!')[0])
        b = float(begin)
        e = float(end)
        db = self._getbandb()
        c = db.cursor()
        try:
            c.execute("""SELECT full FROM nicks WHERE ban_id=%s""",(id))
        except:
            s += ']'
            return s
        L = []
        if c.rowcount:
            bans = c.fetchall()
            for ban in bans:
                L.append(ban)
        if b != e and not remove:
            s += ', for %s' % utils.timeElapsed(e-b)            
        if remove:
            r = float(remove)
            s += ', during %s' % utils.timeElapsed(r-b)
            if len(L) == 0:
                s += ', affected 0 user'
            elif len(L) == 1:
                s += ', affected %s' % L[0][0]
            else:
                s += ', affected %s users' % len(L)
        else:
            if len(L) == 0:
                s += ', affects 0 user'
            elif len(L) == 1:
                s += ', affects %s' % L[0][0].split('!')[0]
            else:
                s += ', affects %s users' % len(L)
        s += ']'
        return s
    
    
    def _markendban (self,irc,channel,oper,id):
        db = self._getbandb()
        channel = channel.lower()
        c = db.cursor()
        ch = self._getChan(irc,channel)
        ch.removed[id] = oper
        try:
            c.execute("""UPDATE bans SET removed_by=%s WHERE id=%s""",(oper,id))
        except:
            return
        db.commit()
    
    def _endban (self,irc,channel,oper,id):
        db = self._getbandb()
        channel = channel.lower()
        c = db.cursor()
        try:
            c.execute("""SELECT id, removed_by FROM bans WHERE id=%s""",id)
            if c.rowcount:
                bans = c.fetchall()
                (id,removed_by) = bans[0]
                if removed_by:
                    oper = removed_by
        except:
            self.log.info('error when trying to find removed_by for %s' % id)
        try:
            c = db.cursor()
            c.execute("""UPDATE bans SET removed_at=%s, removed_by=%s WHERE id=%s""", (time.time(),oper,id))
        except:
            return oper
        db.commit()
        return oper
    
    def _isFlood (self,irc,channel,match,text):
        c = self._getChan(irc,channel)
        key = [match]
        lines = splitmessage(text,300)
        if c.floodCheck:
            if len(lines) > 1:
                for line in lines:
                    c.floodQueue.enqueue(key)
            else:
                c.floodQueue.enqueue(key)
            if c.floodQueue.len(key) > c.floodPermit:
                c.floodQueue.reset(key)
                c.badUserQueue.enqueue([match])
                self.log.info('flood detected for %s in %s' % (match,channel))
                return True
        return False

    def _isLowFlood (self,irc,channel,match,text):
        c = self._getChan(irc,channel)
        key = [match]
        lines = splitmessage(text,300)
        if c.lowFloodCheck:
            if len(lines) > 1:
                for line in lines:
                    c.lowFloodQueue.enqueue(key)
            else:
                c.lowFloodQueue.enqueue(key)
            if c.lowFloodQueue.len(key) > c.lowFloodPermit:
                c.lowFloodQueue.reset(key)
                c.badUserQueue.enqueue([match])
                self.log.info('low flood detected for %s in %s' % (match,channel))
                return True
        return False

    def _isRepeat (self,irc,channel,match,text):
        c = self._getChan(irc,channel)
        key = ['%s%s' % (match,text)]
        lines = splitmessage(text,300)
        if c.repeatCheck:
            if len(lines) > 1:
                for line in lines:
                    c.repeatQueue.enqueue(key)
            else:
                c.repeatQueue.enqueue(key)
            if c.repeatQueue.len(key) > c.repeatPermit:
                c.repeatQueue.reset(key)
                c.badUserQueue.enqueue([match])
                self.log.info('repeat detected for %s in %s' % (match,channel))
                return True
        return False
    
    def _isHighlight (self,irc,channel,match,text):
        c = self._getChan(irc,channel)
        if c.highlightCheck:
            n = 0
            t = text.replace(',','')
            t = t.replace(';','')
            a = t.split(' ')
            for w in a:
                if w in irc.state.channels[channel].users:
                    n +=1
            if n > c.highlightPermit:
                c.badUserQueue.enqueue([match])
                self.log.info('highlight detected for %s in %s' % (match,channel))
                return True
        return False

    def _isCtcp (self,irc,channel,match,text):
        c = self._getChan(irc,channel)
        if c.noticeCheck:
            key = [match]
            s = ''
            c.noticeQueue.enqueue(key)
            if c.noticeQueue.len(key) > c.noticePermit:
                c.noticeQueue.reset(key)
                c.badUserQueue.enqueue(key)
                self.log.info('ctcp detected for %s in %s' % (match,channel))
                return True
        return False

    def doJoin(self, irc, msg):
        try:
            hostmask = irc.state.nickToHostmask(msg.nick)
            mask = getmask(irc,msg.nick)
            bot = irc.state.nickToHostmask(irc.nick)
        except:
            return
        if not mask:
            return
        gateway = isgatewayweb (hostmask)
        ip = False
        try:
            ip = ircdb.isip(mask.split('@')[1])
        except:
            return
        if ip:
            hexa = '*!*%s@*' % iptohexa(mask.split('@')[1])
        now = time.time()
        channels = msg.args[0].split(',')
        for channel in channels:
            c = self._getChan(irc,channel)
            self._doLog(irc,channel,msg.prefix,'*** %s has joined' % msg.nick)
            chan = ircdb.channels.getChannel(channel)
            banned = False
            if chan.bans:
                for ban in chan.bans:
                    if ircutils.hostmaskPatternEqual(ban,hostmask) or ircutils.hostmaskPatternEqual(ban,mask):
                        s = 'kban %s %s %t' % (channel,ban,chan.bans[ban])
                        try:
                            self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                        except:
                            self.log.info('Error %s' % s)
                        banned = True
                        break
            if c.synchro:
                massjoin = False
                if c.massjoinCheck and not c.netsplit:
                    k = [channel]
                    c.massjoinQueue.enqueue(k)
                    if c.massjoinQueue.len(k) > c.massjoinPermit:
                        c.massjoinQueue.reset(k)
                        massjoin = True
                        s = 'mode %s %s' % (channel,c.massjoinMode)
                        if not 'r' in irc.state.channels[channel].modes:
                            try:
                                self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                            except:
                                self.log.info('Error %s' % s)
                            def ur():
                                s = 'mode %s %s' % (channel,c.massjoinUnMode)
                                try:
                                    self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                                except:
                                    self.log.info('Error %s' % s)
                            schedule.addEvent(ur,time.time()+c.massjoinDuration)
                if c.evadeBanCheck and not massjoin and not banned:
                    for ban in irc.state.channels[channel].bans:
                        if ip and hexa:
                            if ircutils.hostmaskPatternEqual(ban,hexa):
                                s = 'kban %s %s %s %s %s' % (channel,msg.nick,c.evadeBanDuration,c.evadeKickMessage,ban)
                                try:
                                    self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                                except:
                                    self.log.info('Error %s' % s)
                                break
                        elif gateway:
                            try:
                                isBanIsIp = ircdb.isip(ban.split('@')[1])
                            except:
                                continue
                            if isBanIsIp:
                                banHexa = '*!*%s@*' % iptohexa(ban.split('@')[1])
                                if ircutils.hostmaskPatternEqual(mask,banHexa):
                                    s = 'kban %s %s %s %s %s' % (channel,msg.nick,c.evadeBanDuration,c.evadeKickMessage,ban)
                                    try:
                                        self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                                    except:
                                        self.log.info('Error %s' % s)
                                    break
                    if c.logChannel in irc.state.channels:
                        for quiet in irc.state.channels[channel].quiets:
                            if mask == quiet:
                                irc.queueMsg(ircmsgs.privmsg(c.logChannel,'%s quieted by %s joins %s' % (msg.prefix,quiet,channel)))
                            elif ip and hexa:
                                if ircutils.hostmaskPatternEqual(quiet,hexa):
                                    irc.queueMsg(ircmsgs.privmsg(c.logChannel,'%s evade quiet %s in %s' % (msg.prefix,quiet,channel)))
                                    break
                            elif gateway:
                                try:
                                    isBanIsIp = ircdb.isip(quiet.split('@')[1])
                                except:
                                    continue
                                if isBanIsIp:
                                    banHexa = '*!*%s@*' % iptohexa(quiet.split('@')[1])
                                    if ircutils.hostmaskPatternEqual(mask,banHexa):
                                        irc.queueMsg(ircmsgs.privmsg(c.logChannel,'%s evade quiet %s in %s' % (msg.prefix,quiet,channel)))
                                        break
                            else:
                               if ircutils.hostmaskPatternEqual(quiet,hostmask):
                                    irc.queueMsg(ircmsgs.privmsg(c.logChannel,'%s quieted by %s joins %s' % (msg.prefix,quiet,channel)))
                               
                if not massjoin:
                    for item in c.regexps:
                        if item[3] == 'JOIN':
                            message = item[2]
                            if item[4].search('%s' % hostmask): 
                                message = message.replace('$nick','"%s"' % msg.nick)
                                message = message.replace('$hostmask',msg.prefix)
                                message = message.replace('$channel',channel)
                                message = message.replace('$mask',mask)
                                if message.find('$randomNick') != -1:
                                    a = []
                                    for user in irc.state.channels[channel].users:
                                        a.append(user)
                                    message = message.replace('$randomNick','"%s"' % a[int(len(a)*random.random())])
                                message = message.lstrip()
                                try:
                                    self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),message.split(' '))
                                except:
                                    self.log.info('Error with #%s in %s' % (item[0],channel))
            

    def doKick(self, irc, msg):
        if len(msg.args) == 3:
            (channel,target,reason) = msg.args
        else:
            (channel,target) = msg.args
            reason = ''
        try:
            hostmask = irc.state.nickToHostmask(target)
        except:
            return
        self._doLog(irc,channel,hostmask,
            '*** %s was kicked by %s (%s)' % (hostmask,msg.nick,reason))
        c = self._getChan(irc,channel)
        if c.logChannel in irc.state.channels:
            irc.queueMsg(ircmsgs.privmsg(c.logChannel,'[%s] %s kicked by %s (%s)' % (channel,hostmask,msg.nick,reason)))
        if target == irc.nick:
            if self.registryValue('alwaysRejoin', channel):
                networkGroup = conf.supybot.networks.get(irc.network)
                irc.sendMsg(networkGroup.channels.join(channel))
                
    def doPart(self, irc, msg):
        try:
            mask = getmask(irc,msg.nick)
            bot = irc.state.nickToHostmask(irc.nick)
        except:
            return
        if not mask:
            return
        reason = ''
        if len(msg.args) == 2:
            (tmp,reason) = msg.args
        channels = msg.args[0].split(',')
        key = [mask]
        for channel in channels:
            c = self._getChan(irc,channel)
            self._doLog(irc,channel,msg.prefix,'*** %s has left [%s]' % (msg.nick,reason))
            if c.cycleCheck:
                c.cycleQueue.enqueue(key)
                if c.cycleQueue.len(key) > c.cyclePermit:
                    c.cycleQueue.reset(key)
                    s = 'kban %s %s %s join/part flood' % (channel,mask,c.cycleBanDuration)
                    try:
                        self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                    except:
                        self.log.info('Error %s' % s)
            if irc.nick == msg.nick:
                self._delChan(irc,channel)
 
        if c.logChannel in irc.state.channels and reason.find('requested by') != -1:
            irc.queueMsg(ircmsgs.privmsg(c.logChannel,'[%s] part %s %s' % (channel,msg.nick,reason))) 

   
    def doQuit(self, irc, msg):
        try:
            hostmask = irc.state.nickToHostmask(msg.nick)
            mask = getmask(irc,msg.nick)
            bot = irc.state.nickToHostmask(irc.nick)
        except:
            return
        if not mask:
            return
        reason = ''
        if len(msg.args) == 1:
            reason = msg.args[0].lstrip().rstrip()
        isSplit = reason == '*.net *.split'
        isCloak = reason == 'Changing host'
        isFlood = reason == 'Excess Flood'
        key = [mask]
        for channel in irc.state.channels:
            c = self._getChan(irc,channel)
            if isSplit and not c.netsplit:
                def us():
                    c.netsplit = False
                schedule.addEvent(us,900)
                c.netsplit = True
            if msg.nick in c.nicks:
                self._doLog(irc,channel,msg.prefix,'*** %s has quit [%s]' % (msg.nick,reason)) 
            if c.cycleCheck and not isSplit and not isCloak:
                if not isSplit and not isCloak:
                    c.cycleQueue.enqueue(key)
                    if c.cycleQueue.len(key) > c.cyclePermit:
                        c.cycleQueue.reset(key)
                        s = 'kban %s %s %s join/part flood' % (channel,mask,c.cycleBanDuration)
                        try:
                            self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                        except:
                            self.log.info('Error %s' % s)
            if isFlood:
                c.cycleQueue.enqueue(key)
                if c.cycleQueue.len(key) > c.cyclePermit:
                    c.cycleQueue.reset(key)
                    s = 'kban %s %s %s join/part flood' % (channel,mask,c.cycleBanDuration)
                    try:
                        self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                    except:
                        self.log.info('Error %s' % s)
  

                      
    def doPrivmsg(self, irc, msg):
        if msg.nick == irc.nick:
            return
        try:
            hostmask = irc.state.nickToHostmask(msg.nick)
            mask = getmask(irc,msg.nick)
            bot = irc.state.nickToHostmask(irc.nick)
        except:
            return
        if not mask:
            return
        isCtcp = ircmsgs.isCtcp(msg)
        (recipients, text) = msg.args
        isAction = False
        if ircmsgs.isAction(msg):
            isAction = True
            text = ircmsgs.unAction(msg)
        for channel in recipients.split(','):
            if irc.isChannel(channel):
                if isAction:
                    self._doLog(irc,channel,msg.prefix,'* %s %s' % (msg.nick,text))
                else:
                    self._doLog(irc,channel,msg.prefix,'<%s> %s' % (msg.nick, text))
                c = self._getChan(irc,channel)
                underAttack = False
                if c.synchro:
                    s = None
                    isClone = False
                    a = self._check(irc,channel,mask)
                    check = ircdb.ignores.checkIgnored(mask)
                    isrepeat = False
                    if self._isFlood(irc,channel,hostmask,text):
                        if check or mask in irc.state.channels[channel].quiets:
                            c.badUserQueue.enqueue([mask])
                        else:
                            s = 'quiet %s %s %s %s' % (channel,msg.nick,c.floodQuietDuration,c.floodMessage)
                            if not ircdb.checkCapability(msg.prefix, 'owner'):
                                ircdb.ignores.add(hostmask, time.time()+c.floodQuietDuration)
                                check = True
                    elif self._isFlood(irc,channel,mask,text) and len(a) > 1 and not ircdb.ignores.checkIgnored(hostmask):
                        isClone = True
                        
                    if self._isLowFlood(irc,channel,hostmask,text):
                        if check or mask in irc.state.channels[channel].quiets:
                            c.badUserQueue.enqueue([mask])
                        else:
                            s = 'quiet %s %s %s %s' % (channel,msg.nick,c.lowFloodQuietDuration,c.floodMessage)
                            if not ircdb.checkCapability(msg.prefix, 'owner'):
                                ircdb.ignores.add(hostmask, time.time()+c.lowFloodQuietDuration)
                                check = True
                    elif self._isLowFlood(irc,channel,mask,text) and len(a) > 1 and not ircdb.ignores.checkIgnored(hostmask):
                        isClone = True
                        
                    if self._isRepeat(irc,channel,hostmask,text):
                        if check or mask in irc.state.channels[channel].quiets:
                            c.badUserQueue.enqueue([mask])
                        else:
                            s = 'quiet %s %s %s %s' % (channel,msg.nick,c.repeatQuietDuration,c.repeatMessage)
                            isrepeat = True
                            if not ircdb.checkCapability(msg.prefix, 'owner'):
                                ircdb.ignores.add(hostmask, time.time()+c.repeatQuietDuration)
                                check = True
                    elif self._isRepeat(irc,channel,mask,text) and len(a) > 1 and not ircdb.ignores.checkIgnored(hostmask):
                        isClone = True
                    
                    if self._isHighlight(irc,channel,hostmask,text):
                        if check or mask in irc.state.channels[channel].quiets:
                            c.badUserQueue.enqueue([mask])
                        else:
                            s = 'quiet %s %s %s %s' % (channel,msg.nick,c.highlightQuietDuration,c.highlightMessage)
                            if not ircdb.checkCapability(msg.prefix, 'owner'):
                                ircdb.ignores.add(hostmask, time.time()+c.highlightQuietDuration)
                                check = True
                    elif self._isHighlight(irc,channel,mask,text) and len(a) > 1 and not ircdb.ignores.checkIgnored(hostmask):
                        isClone = True
                    
                    if isCtcp and not isAction:
                        if self._isCtcp(irc,channel,hostmask,text):
                            if check or mask in irc.state.channels[channel].quiets:
                                c.badUserQueue.enqueue([mask])
                            else:
                                s = 'quiet %s %s %s %s' % (channel,msg.nick,c.noticeQuietDuration,c.noticeMessage)
                                if not ircdb.checkCapability(msg.prefix, 'owner'):
                                    ircdb.ignores.add(hostmask, time.time()+c.noticeQuietDuration)
                                    check = True
                        elif self._isCtcp(irc,channel,mask,text) and len(a) > 1 and not ircdb.ignores.checkIgnored(hostmask):
                            isClone = True
                    
                    key = [mask]

                    if c.badUserQueue.len(key) > c.badUserPermit:
                        c.badUserQueue.reset(key)
                        s = 'kban %s %s %s %s' % (channel,msg.nick,c.badUserBanDuration,c.badUserMessage)
                        if not ircdb.checkCapability(msg.prefix, 'owner'):
                            ircdb.ignores.add(mask, time.time()+c.badUserBanDuration)
                            check = True
                        if isrepeat:
                            reg = 'regadd %s /%s/i @ kban $channel $nick %s %s' % (channel,text,c.badUserBanDuration,c.badUserMessage)
                            try:
                                self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),reg.split(' '))
                                def liftreg():
                                    ureg = 'regdel %s /%s/i' % (channel,text)
                                    try:
                                        self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),ureg.split(' '))
                                    except:
                                        self.log.info('Error with %s' % ureg)
                                schedule.addEvent(liftreg,time.time()+c.badUserBanDuration)
                            except:
                                self.log.info('Error with %s' % reg)
                    elif isClone and len(a) > 1:
                        s = 'kban %s %s %s %s' % (channel,msg.nick,c.badUserBanDuration,c.badUserMessage)
                        if not ircdb.checkCapability(msg.prefix, 'owner'):
                            ircdb.ignores.add(mask, time.time()+c.badUserBanDuration)
                            check = True                        
                    if s:
                        try:
                            self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                        except:
                            self.log.info('Error with %s' % s)
                        if s.startswith('kban'):
                            c.attacks.enqueue([channel])
                            if c.attacks.len([channel]) > 3 and not 'r' in irc.state.channels[channel].modes:
                                irc.sendMsg(ircmsgs.IrcMsg('MODE %s +r-z+q $~a' % channel))
                                underAttack = True
                                c.attacks.reset([channel])
                                if c.opChannel in irc.state.channels:
                                    s = 'ops %s emergency modes for 15 minutes (+r-z+q $~a)' % channel
                                    try:
                                        self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                                    except:
                                        self.log.info('Error with %s' % s)
                                def ua():
                                    irc.sendMsg(ircmsgs.IrcMsg('MODE %s -r+z-q $~a' % channel))
                                schedule.addEvent(ua,time.time()+900)
                                
                    if c.opChannel in irc.state.channels and isClone and not underAttack and len(a) > 1:
                        s = 'ops %s clones attacks: %s' % (len(a),', '.join(a))
                        try:
                            self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                        except:
                            self.log.info('Error with %s' % s)
                    if c.logChannel in irc.state.channels and not isClone and not underAttack and not check:
                        s = None
                        m = None
                        if isAction:
                            s = '* %s %s' % (msg.nick,text)
                        else:
                            s = '<%s> %s' % (msg.nick,text)
                        if 'm' in irc.state.channels[channel].modes:
                            if not msg.nick in irc.state.channels[channel].voices and not msg.nick in irc.state.channels[channel].ops:
                                m = '+m'
                        else:
                            if 'z' in irc.state.channels[channel].modes:
                                for ban in irc.state.channels[channel].bans:
                                    if ircutils.hostmaskPatternEqual(ban,msg.prefix):
                                        m = '+b'
                                        break
                                if not m:
                                    for quiet in irc.state.channels[channel].quiets:
                                        if ircutils.hostmaskPatternEqual(quiet,msg.prefix):
                                            m = '+q'
                                            break
                        if m:
                            s = '[%s](%s) %s' % (channel,m,s)
                            irc.queueMsg(ircmsgs.privmsg(c.logChannel,s))
                    raw = '%s %s %s' % (msg.prefix,mask,text)
                    warned = False
                    if msg.addressed and c.commandCheck:
                        key = msg.addressed.split(' ')[0]
                        c.commandQueue.enqueue([key])
                        if c.commandQueue.len([key]) > c.commandPermit:
                            s = 'channel disable %s' % key
                            c.commandQueue.reset([key])
                            try:
                                self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                                s = 'channel enable %s' % key
                                def ui():
                                    try:
                                        self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                                    except:
                                        self.log.info('Error with %s in %s' % (s,channel))
                                schedule.addEvent(ui,time.time()+c.commandDisableDuration)
                            except:
                                self.log.info('Error with %s in %s' % (s,channel))
                            
                    if not check and not isClone and not underAttack:
                        for item in c.regexps:
                            if item[3] == 'TEXT':
                                message = item[2].lstrip().rstrip()
                                if item[4].search(raw): 
                                    message = message.replace('$channel',channel)
                                    message = message.replace('$nick','"%s"' % msg.nick)
                                    message = message.replace('$hostmask',msg.prefix)
                                    message = message.replace('$mask',mask)
                                    message = message.replace('$text',text)
                                    if message.find('$randomNick') != -1:
                                        a = []
                                        for user in irc.state.channels[channel].users:
                                            a.append(user)
                                        message = message.replace('$randomNick','"%s"' % a[int(len(a)*random.random())])
                                    message = message.lstrip()
                                    a = message.split(' ')
                                    a[0] = a[0].lstrip()
                                    try:
                                        self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),a)
                                    except:
                                        self.log.info('Error with #%s in %s' % (item[0],channel)) 
                            elif item[3] == 'WARN' and not warned:
                                cu = self._getUser(irc,channel,msg.prefix)
                                if cu:
                                    if item[4].search(raw):
                                        message = item[2]
                                        a = message.split(' ')
                                        if a[0].lstrip().rstrip().isdigit():
                                            i = int(a[0].lstrip().rstrip())
                                            n = cu.warns+1
                                            if n == i:
                                                cu.warns = cu.warns+1
                                                warned = True
                                                a.pop(0)
                                                message = ' '.join(a).lstrip().rstrip()
                                                message = message.replace('$channel',channel)
                                                message = message.replace('$nick','"%s"' % msg.nick)
                                                message = message.replace('$hostmask',msg.prefix)
                                                message = message.replace('$mask',mask)
                                                message = message.replace('$text',text)
                                                if message.find('$randomNick') != -1:
                                                    a = []
                                                    for user in irc.state.channels[channel].users:
                                                        a.append(user)
                                                    message = message.replace('$randomNick','"%s"' % a[int(len(a)*random.random())])
                                                message = message.lstrip()
                                                a = message.split(' ')
                                                a[0] = a[0].lstrip().rstrip()
                                                try:
                                                    self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),a)
                                                except:
                                                    self.log.info('Error with #%s in %s' % (item[0],channel)) 
                                             
    def doNotice(self, irc, msg):
        if ircmsgs.isCtcp(msg):
            self.log.info('CTCP received from %s' % msg.prefix)
        (recipients, text) = msg.args
        if msg.nick == irc.nick:
            return
        try:
            hostmask = irc.state.nickToHostmask(msg.nick)
            mask = getmask(irc,msg.nick)
            bot = irc.state.nickToHostmask(irc.nick)
        except:
            return
        if not mask:
            return
        for channel in recipients.split(','):
            if irc.isChannel(channel):
                self._doLog(irc,channel,msg.prefix,'-%s- %s' % (msg.nick,text))
                c = self._getChan(irc,channel)
                if c.synchro:
                    if c.noticeCheck:
                        key = [mask]
                        s = ''
                        c.noticeQueue.enqueue(key)
                        if c.noticeQueue.len(key) > c.noticePermit:
                            c.noticeQueue.reset(key)
                            s = 'quiet %s %s %s %s' % (channel,msg.nick,c.noticeQuietDuration,c.noticeMessage)
                            c.badUserQueue.enqueue(key)
                            if c.badUserQueue.len(key) > c.badUserPermit:
                                c.badUserQueue.reset(key)
                                s = 'kban %s %s %s %s' % (channel,msg.nick,c.badUserBanDuration,c.badUserMessage)
                            else:
                                if not ircdb.checkCapability(msg.prefix, 'owner'):
                                    ircdb.ignores.add(mask, time.time()+c.noticeQuietDuration)
                            self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                            if s.startswith('kban'):
                                a = self._check(irc,channel,mask)
                                if a and len(a) > 1:
                                    for u in a:
                                        s = 'kick %s %s %s' % (channel,u.split('!')[0],c.badUserMessage)
                                        self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),s.split(' '))
                    if c.logChannel in irc.state.channels:
                        irc.queueMsg(ircmsgs.privmsg(c.logChannel,'[%s] -%s- %s' % (channel,msg.prefix,text)))
                
    def doNick(self, irc, msg):
        oldNick = msg.nick
        newNick = msg.args[0]
        (n, u, h) = ircutils.splitHostmask(msg.prefix)
        prefix = ircutils.joinHostmask(newNick,u,h)
        for (channel, ch) in irc.state.channels.iteritems():
            if newNick in ch.users:
                c = self._getChan(irc,channel)
                if oldNick in c.nicks:
                    c.nicks[newNick] = c.nicks[oldNick]
                    c.nicks[newNick].host = msg.prefix
                    del c.nicks[oldNick]
                self._doLog(irc,channel,prefix,
                           '*** %s is now known as %s' % (oldNick,newNick))
                if c.synchro:
                    for item in c.regexps:
                        if item[3] == 'NICK':
                            message = item[2]
                            if item[4].search(prefix): 
                                message = message.replace('$nick','"%s"' % newNick)
                                message = message.replace('$hostmask',msg.prefix)
                                message = message.replace('$channel',channel)
                                message = message.replace('$mask',mask)
                                if message.find('$randomNick') != -1:
                                    a = []
                                    for user in irc.state.channels[channel].users:
                                        a.append(user)
                                    message = message.replace('$randomNick','"%s"' % a[int(len(a)*random.random())])
                                message = message.lstrip()
                                try:
                                    self.Proxy(irc,ircmsgs.IrcMsg(prefix=irc.prefix,msg=msg),message.split(' '))
                                except:
                                    self.log.info('Error with #%s in %s' % (item[0],channel))
                           
    def doMode(self, irc, msg):
        channel = msg.args[0]
        if irc.isChannel(channel) and msg.args[1:]:
            self._doLog(irc,channel,msg.prefix,
                       '*** %s sets mode: %s %s' % (msg.nick,msg.args[1],' '.join(msg.args[2:])))
            try:
                mask = irc.state.nickToHostmask(msg.nick)
            except:
                return
            c = self._getChan(irc,channel)
            now = time.time()
            if channel in irc.state.channels and msg.args[1:]:
                if irc.state.channels[channel].synchro:
                    modes = ircutils.separateModes(msg.args[1:])
                    a = []
					
                    for mode in modes:
                        (kind,value) = mode
                        id = None
                        if '+' in kind:
                            if 'b' in kind:
                                if not value in c.activeBans:
                                    id = self._addban(irc,channel,msg.prefix,'b',value,0)
                                    self._addbanaffects(irc,channel,id,'b',value)
                                    c.activeBans[str(value)] = (id,msg.prefix,'b',value,now,now)
                                    if msg.nick != irc.nick and msg.nick != 'ChanServ':
                                        try:
                                            user = ircdb.users.getUser(msg.prefix)
                                        except KeyError:
                                            user = None
                                        if user:
                                            irc.queueMsg(ircmsgs.privmsg(msg.nick,"About [#%s +b %s in %s] you can use !banmark %s or !banedit %s" % (id,value,channel,id,id)))
                                    a.append(self._summaryBan(irc,channel,msg.prefix,id,kind[1],kind[0],value,now,now,None))
                                else:
                                    (id,by,k,mask,begin,end) = c.activeBans[str(value)]  
                                    a.append(self._summaryBan(irc,channel,by,id,kind[1],kind[0],mask,begin,end,None))
                                chan = ircdb.channels.getChannel(channel)
                                chan.addIgnore(value, 0)
                                ircdb.channels.setChannel(channel, chan)
                            elif 'q' in kind:
                                if not value in c.activeQuiets:
                                    id = self._addban(irc,channel,msg.prefix,'q',value,0)
                                    self._addbanaffects(irc,channel,id,'q',value)
                                    c.activeQuiets[str(value)] = (id,msg.prefix,'q',value,now,now)
                                    if msg.nick != irc.nick and msg.nick != 'ChanServ':
                                        try:
                                            user = ircdb.users.getUser(msg.prefix)
                                        except KeyError:
                                            user = None
                                        if user:
                                            irc.queueMsg(ircmsgs.privmsg(msg.nick,"About [#%s +q %s in %s] you can use !banmark %s or !banedit %s" % (id,value,channel,id,id)))
                                    a.append(self._summaryBan(irc,channel,msg.prefix,id,kind[1],kind[0],value,now,now,None))
                                else:
                                    (id,by,k,mask,begin,end) = c.activeQuiets[str(value)]  
                                    a.append(self._summaryBan(irc,channel,by,id,kind[1],kind[0],mask,begin,end,None))
                                chan = ircdb.channels.getChannel(channel)
                                chan.addIgnore(value, 0)
                                ircdb.channels.setChannel(channel, chan)
                            else:
                                if value:
                                    a.append('%s %s' % (kind,value))
                                else:
                                    a.append('%s' % kind)
                        else:
                            if 'b' in kind:
                                if value in c.activeBans:
                                    (id,by,k,mask,begin,end) = c.activeBans[value]
                                    self._endban(irc,channel,msg.prefix,id)
                                    per = msg.prefix
                                    if id in c.removed:
                                        per = c.removed[id]
                                    s = self._summaryBan(irc,channel,per,id,kind[1],kind[0],mask,begin,end,now)
                                    if per != msg.nick:
                                        a.append(s)
                                    else:
                                        s = s.replace('by %s' % msg.nick,'')
                                        a.append(s)
                                    if mask in c.pendingBans:
                                        name = c.pendingBans[mask]
                                        if name in c.schedules:
                                            try:
                                                schedule.removeEvent(name)
                                                del c.schedules[name]
                                            except:
                                                del c.schedules[name]
                                        del c.pendingBans[mask]
                                    del c.activeBans[mask]
                                else:
                                    a.append('%s %s' % (kind,value))
                                chan = ircdb.channels.getChannel(channel)
                                try:
                                    chan.removeIgnore(value)
                                    ircdb.channels.setChannel(channel, chan)
                                except KeyError:
                                    self.log.info('error when removing %s of %s ignore list' % (value,channel))
                            elif 'q' in kind:
                                if value in c.activeQuiets:
                                    (id,by,k,mask,begin,end) = c.activeQuiets[value]
                                    self._endban(irc,channel,msg.prefix,id)
                                    per = msg.prefix
                                    if id in c.removed:
                                        per = c.removed[id]
                                    s = self._summaryBan(irc,channel,per,id,kind[1],kind[0],mask,begin,end,now)
                                    if per != msg.nick:
                                        a.append(s)
                                    else:
                                        s = s.replace('by %s' % msg.nick,'')
                                        a.append(s)
                                    if mask in c.pendingQuiets:
                                        name = c.pendingQuiets[mask]
                                        if name in c.schedules:
                                            try:
                                                schedule.removeEvent(name)
                                                del c.schedules[name]
                                            except:
                                                del c.schedules[name]
                                        del c.pendingQuiets[mask]
                                    del c.activeQuiets[mask]
                                else:
                                    a.append('%s %s' % (kind,value))
                                chan = ircdb.channels.getChannel(channel)
                                try:
                                    chan.removeIgnore(value)
                                    ircdb.channels.setChannel(channel, chan)
                                except KeyError:
                                    self.log.info('error when removing %s of %s ignore list' % (value,channel))
                            else:
                                if value:
                                    a.append('%s %s' % (kind,value))
                                else:
                                    a.append('%s' % kind)
                    if c.logChannel in irc.state.channels:
                        if len(' '.join(a)) > 380:
                            i = 0
                            while i < len(a):
                                r = [a[i],a[int(i+1)]]
                                irc.queueMsg(ircmsgs.privmsg(c.logChannel,'%s sets mode in %s : %s' % (msg.nick,channel,' '.join(r))))
                                i = i+2
                        else:
                            irc.queueMsg(ircmsgs.privmsg(c.logChannel,'%s sets mode in %s : %s' % (msg.nick,channel,' '.join(a))))
                            
    def do341(self, irc, msg):
        (_, nick, channel) = msg.args
        nick = ircutils.toLower(nick)
        replyIrc = self.invites.pop((irc, nick), None)
        if replyIrc is not None:
            self.log.info('Inviting %s to %s by command of %s.',
                          nick, channel, replyIrc.msg.prefix)
            replyIrc.replySuccess()
        else:
            self.log.info('Inviting %s to %s.', nick, channel)

    def do443(self, irc, msg):
        (_, nick, channel, _) = msg.args
        nick = ircutils.toLower(nick)
        replyIrc = self.invites.pop((irc, nick), None)
        if replyIrc is not None:
            replyIrc.error(format('%s is already in %s.', nick, channel))

    def do401(self, irc, msg):
        nick = msg.args[1]
        nick = ircutils.toLower(nick)
        replyIrc = self.invites.pop((irc, nick), None)
        if replyIrc is not None:
            replyIrc.error(format('There is no %s on this network.', nick))

    def do504(self, irc, msg):
        nick = msg.args[1]
        nick = ircutils.toLower(nick)
        replyIrc = self.invites.pop((irc, nick), None)
        if replyirc is not None:
            replyIrc.error(format('There is no %s on this server.', nick))

    class lobotomy(callbacks.Commands):
        def add(self, irc, msg, args, channel):
            """[<channel>]

            If you have the #channel,op capability, this will "lobotomize" the
            bot, making it silent and unanswering to all requests made in the
            channel. <channel> is only necessary if the message isn't sent in
            the channel itself.
            """
            c = ircdb.channels.getChannel(channel)
            c.lobotomized = True
            ircdb.channels.setChannel(channel, c)
            irc.replySuccess()
        add = wrap(add, ['op'])

        def remove(self, irc, msg, args, channel):
            """[<channel>]

            If you have the #channel,op capability, this will unlobotomize the
            bot, making it respond to requests made in the channel again.
            <channel> is only necessary if the message isn't sent in the channel
            itself.
            """
            c = ircdb.channels.getChannel(channel)
            c.lobotomized = False
            ircdb.channels.setChannel(channel, c)
            irc.replySuccess()
        remove = wrap(remove, ['op'])

        def list(self, irc, msg, args):
            """takes no arguments

            Returns the channels in which this bot is lobotomized.
            """
            L = []
            for (channel, c) in ircdb.channels.iteritems():
                if c.lobotomized:
                    chancap = ircdb.makeChannelCapability(channel, 'op')
                    if ircdb.checkCapability(msg.prefix, 'admin') or \
                       ircdb.checkCapability(msg.prefix, chancap) or \
                       (channel in irc.state.channels and \
                        msg.nick in irc.state.channels[channel].users):
                        L.append(channel)
            if L:
                L.sort()
                s = format('I\'m currently lobotomized in %L.', L)
                irc.reply(s)
            else:
                irc.reply('I\'m not currently lobotomized in any channels '
                          'that you\'re in.')
        list = wrap(list)

    class ban(callbacks.Commands):
        def add(self, irc, msg, args, channel, banmask, expires):
            """[<channel>] <nick|hostmask> [<expires>]

            If you have the #channel,op capability, this will effect a
            persistent ban from interacting with the bot on the given
            <hostmask> (or the current hostmask associated with <nick>.  Other
            plugins may enforce this ban by actually banning users with
            matching hostmasks when they join.  <expires> is an optional
            argument specifying when (in "seconds from now") the ban should
            expire; if none is given, the ban will never automatically expire.
            <channel> is only necessary if the message isn't sent in the
            channel itself.
            """
            c = ircdb.channels.getChannel(channel)
            c.addBan(banmask, expires)
            ircdb.channels.setChannel(channel, c)
            irc.replySuccess()
        add = wrap(add, ['op', 'banmask', additional('expiry', 0)])

        def remove(self, irc, msg, args, channel, banmask):
            """[<channel>] <hostmask>

            If you have the #channel,op capability, this will remove the
            persistent ban on <hostmask>.  <channel> is only necessary if the
            message isn't sent in the channel itself.
            """
            c = ircdb.channels.getChannel(channel)
            try:
                c.removeBan(banmask)
                ircdb.channels.setChannel(channel, c)
                irc.replySuccess()
            except KeyError:
                irc.error('There are no persistent bans for that hostmask.')
        remove = wrap(remove, ['op', 'hostmask'])

        def list(self, irc, msg, args, channel):
            """[<channel>]

            If you have the #channel,op capability, this will show you the
            current persistent bans on #channel.
            """
            c = ircdb.channels.getChannel(channel)
            if c.bans:
                bans = []
                for ban in c.bans:
                    if c.bans[ban]:
                        bans.append(format('%q (expires %t)',
                                           ban, c.bans[ban]))
                    else:
                        bans.append(format('%q (never expires)',
                                           ban, c.bans[ban]))
                irc.reply(format('%L', bans))
            else:
                irc.reply(format('There are no persistent bans on %s.',
                                 channel))
        list = wrap(list, ['op'])

    class ignore(callbacks.Commands):
        def add(self, irc, msg, args, channel, banmask, expires):
            """[<channel>] <nick|hostmask> [<expires>]

            If you have the #channel,op capability, this will set a persistent
            ignore on <hostmask> or the hostmask currently
            associated with <nick>. <expires> is an optional argument
            specifying when (in "seconds from now") the ignore will expire; if
            it isn't given, the ignore will never automatically expire.
            <channel> is only necessary if the message isn't sent in the
            channel itself.
            """
            c = ircdb.channels.getChannel(channel)
            c.addIgnore(banmask, expires)
            ircdb.channels.setChannel(channel, c)
            irc.replySuccess()
        add = wrap(add, ['op', 'banmask', additional('expiry', 0)])

        def remove(self, irc, msg, args, channel, banmask):
            """[<channel>] <hostmask>

            If you have the #channel,op capability, this will remove the
            persistent ignore on <hostmask> in the channel. <channel> is only
            necessary if the message isn't sent in the channel itself.
            """
            c = ircdb.channels.getChannel(channel)
            try:
                c.removeIgnore(banmask)
                ircdb.channels.setChannel(channel, c)
                irc.replySuccess()
            except KeyError:
                irc.error('There are no ignores for that hostmask.')
        remove = wrap(remove, ['op', 'hostmask'])

        def list(self, irc, msg, args, channel):
            """[<channel>]

            Lists the hostmasks that the bot is ignoring on the given channel.
            <channel> is only necessary if the message isn't sent in the
            channel itself.
            """
            # XXX Add the expirations.
            c = ircdb.channels.getChannel(channel)
            if len(c.ignores) == 0:
                s = format('I\'m not currently ignoring any hostmasks in %q',
                           channel)
                irc.reply(s)
            else:
                L = sorted(c.ignores)
                irc.reply(utils.str.commaAndify(map(repr, L)))
        list = wrap(list, ['op'])

    class capability(callbacks.Commands):
        def add(self, irc, msg, args, channel, user, capabilities):
            """[<channel>] <nick|username> <capability> [<capability> ...]

            If you have the #channel,op capability, this will give the user
            <name> (or the user to whom <nick> maps)
            the capability <capability> in the channel. <channel> is only
            necessary if the message isn't sent in the channel itself.
            """
            for c in capabilities.split():
                c = ircdb.makeChannelCapability(channel, c)
                user.addCapability(c)
            ircdb.users.setUser(user)
            irc.replySuccess()
        add = wrap(add, ['op', 'otherUser', 'capability'])

        def remove(self, irc, msg, args, channel, user, capabilities):
            """[<channel>] <name|hostmask> <capability> [<capability> ...]

            If you have the #channel,op capability, this will take from the
            user currently identified as <name> (or the user to whom <hostmask>
            maps) the capability <capability> in the channel. <channel> is only
            necessary if the message isn't sent in the channel itself.
            """
            fail = []
            for c in capabilities.split():
                cap = ircdb.makeChannelCapability(channel, c)
                try:
                    user.removeCapability(cap)
                except KeyError:
                    fail.append(c)
            ircdb.users.setUser(user)
            if fail:
                s = 'capability'
                if len(fail) > 1:
                    s = utils.str.pluralize(s)
                irc.error(format('That user didn\'t have the %L %s.', fail, s),
                          Raise=True)
            irc.replySuccess()
        remove = wrap(remove, ['op', 'otherUser', 'capability'])

        # XXX This needs to be fix0red to be like Owner.defaultcapability.  Or
        # something else.  This is a horrible interface.
        def setdefault(self, irc, msg, args, channel, v):
            """[<channel>] {True|False}

            If you have the #channel,op capability, this will set the default
            response to non-power-related (that is, not {op, halfop, voice}
            capabilities to be the value you give. <channel> is only necessary
            if the message isn't sent in the channel itself.
            """
            c = ircdb.channels.getChannel(channel)
            if v:
                c.setDefaultCapability(True)
            else:
                c.setDefaultCapability(False)
            ircdb.channels.setChannel(channel, c)
            irc.replySuccess()
        setdefault = wrap(setdefault, ['op', 'boolean'])

        def set(self, irc, msg, args, channel, capabilities):
            """[<channel>] <capability> [<capability> ...]

            If you have the #channel,op capability, this will add the channel
            capability <capability> for all users in the channel. <channel> is
            only necessary if the message isn't sent in the channel itself.
            """
            chan = ircdb.channels.getChannel(channel)
            for c in capabilities:
                chan.addCapability(c)
            ircdb.channels.setChannel(channel, chan)
            irc.replySuccess()
        set = wrap(set, ['op', many('capability')])

        def unset(self, irc, msg, args, channel, capabilities):
            """[<channel>] <capability> [<capability> ...]

            If you have the #channel,op capability, this will unset the channel
            capability <capability> so each user's specific capability or the
            channel default capability will take precedence. <channel> is only
            necessary if the message isn't sent in the channel itself.
            """
            chan = ircdb.channels.getChannel(channel)
            fail = []
            for c in capabilities:
                try:
                    chan.removeCapability(c)
                except KeyError:
                    fail.append(c)
            ircdb.channels.setChannel(channel, chan)
            if fail:
                s = 'capability'
                if len(fail) > 1:
                    s = utils.str.pluralize(s)
                irc.error(format('I do not know about the %L %s.', fail, s),
                          Raise=True)
            irc.replySuccess()
        unset = wrap(unset, ['op', many('capability')])

        def list(self, irc, msg, args, channel):
            """[<channel>]

            Returns the capabilities present on the <channel>. <channel> is
            only necessary if the message isn't sent in the channel itself.
            """
            c = ircdb.channels.getChannel(channel)
            L = sorted(c.capabilities)
            irc.reply(' '.join(L))
        list = wrap(list, ['channel'])

    def disable(self, irc, msg, args, channel, plugin, command):
        """[<channel>] [<plugin>] [<command>]

        If you have the #channel,op capability, this will disable the <command>
        in <channel>.  If <plugin> is provided, <command> will be disabled only
        for that plugin.  If only <plugin> is provided, all commands in the
        given plugin will be disabled.  <channel> is only necessary if the
        message isn't sent in the channel itself.
        """
        chan = ircdb.channels.getChannel(channel)
        failMsg = ''
        if plugin:
            s = '-%s' % plugin.name()
            if command:
                if plugin.isCommand(command):
                    s = '-%s.%s' % (plugin.name(), command)
                else:
                    failMsg = format('The %s plugin does not have a command '
                                     'called %s.', plugin.name(), command)
        elif command:
            # findCallbackForCommand
            if filter(None, irc.findCallbacksForArgs([command])):
                s = '-%s' % command
            else:
                failMsg = format('No plugin or command named %s could be '
                                 'found.', command)
        else:
            raise callbacks.ArgumentError
        if failMsg:
            irc.error(failMsg)
        else:
            chan.addCapability(s)
            ircdb.channels.setChannel(channel, chan)
            irc.replySuccess()
    disable = wrap(disable, ['op',
                             optional(('plugin', False)),
                             additional('commandName')])

    def enable(self, irc, msg, args, channel, plugin, command):
        """[<channel>] [<plugin>] [<command>]

        If you have the #channel,op capability, this will enable the <command>
        in <channel> if it has been disabled.  If <plugin> is provided,
        <command> will be enabled only for that plugin.  If only <plugin> is
        provided, all commands in the given plugin will be enabled.  <channel>
        is only necessary if the message isn't sent in the channel itself.
        """
        chan = ircdb.channels.getChannel(channel)
        failMsg = ''
        if plugin:
            s = '-%s' % plugin.name()
            if command:
                if plugin.isCommand(command):
                    s = '-%s.%s' % (plugin.name(), command)
                else:
                    failMsg = format('The %s plugin does not have a command '
                                     'called %s.', plugin.name(), command)
        elif command:
            # findCallbackForCommand
            if filter(None, irc.findCallbacksForArgs([command])):
                s = '-%s' % command
            else:
                failMsg = format('No plugin or command named %s could be '
                                 'found.', command)
        else:
            raise callbacks.ArgumentError
        if failMsg:
            irc.error(failMsg)
        else:
            fail = []
            try:
                chan.removeCapability(s)
            except KeyError:
                fail.append(s)
            ircdb.channels.setChannel(channel, chan)
            if fail:
                irc.error(format('%s was not disabled.', s[1:]))
            else:
                irc.replySuccess()
    enable = wrap(enable, ['op',
                           optional(('plugin', False)),
                           additional('commandName')])

    def nicks(self, irc, msg, args, channel):
        """[<channel>]

        Returns the nicks in <channel>.  <channel> is only necessary if the
        message isn't sent in the channel itself.
        """
        # Make sure we don't elicit information about private channels to
        # people or channels that shouldn't know
        if 's' in irc.state.channels[channel].modes and \
            msg.args[0] != channel and \
            (ircutils.isChannel(msg.args[0]) or \
             msg.nick not in irc.state.channels[channel].users):
            irc.error('You don\'t have access to that information.')
        L = list(irc.state.channels[channel].users)
        utils.sortBy(str.lower, L)
        irc.reply(utils.str.commaAndify(L))
    nicks = wrap(nicks, ['inChannel'])

    def alertOps(self, irc, channel, s, frm=None):
        """Internal message for notifying all the #channel,ops in a channel of
        a given situation."""
        capability = ircdb.makeChannelCapability(channel, 'op')
        s = format('Alert to all %s ops: %s', channel, s)
        if frm is not None:
            s += format(' (from %s)', frm)
        for nick in irc.state.channels[channel].users:
            hostmask = irc.state.nickToHostmask(nick)
            if ircdb.checkCapability(hostmask, capability):
                irc.reply(s, to=nick, private=True)

    def alert(self, irc, msg, args, channel, text):
        """[<channel>] <text>

        Sends <text> to all the users in <channel> who have the <channel>,op
        capability.
        """
        self.alertOps(irc, channel, text, frm=msg.nick)
    alert = wrap(alert, ['op', 'text'])

Class = Channel

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
