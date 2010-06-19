###
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
import random

try:
    import sqlite
except ImportError:
    raise callbacks.Error, 'You need to have PySQLite installed to use this plugin. \
        Download it at <http://pysqlite.org/>'

class Chan (object):
    def __init__(self):
        object.__init__(self)
        self.category = None
        self.current = None
        
class Session (object):
    def __init__(self):
        object.__init__(self)
        self.channel = None
        self.hostmask = None
        self.category = None
        self.question = None
        self.answers = []
        self.answered = None
        self.valid = []

class Game (object):
    def __init__(self):
        object.__init__(self)
        self.channel = None
        self.category = None
        self.question = None
        self.owner = None
        self.players = {}
        self.answers = []        
        self.good = None
        self.end = False

class Quizz(callbacks.Plugin,plugins.ChannelDBHandler):
    """Add the help for "@plugin help Quizz" here
    This should describe *how* to use this plugin."""
    threaded = True
    def __init__(self, irc):
        self.__parent = super(Quizz, self)
        self.__parent.__init__(irc)
        self.pending = {}
        self.dbCache = {}
        self.game = {}
    
    def makeDb(self, filename):
        if os.path.exists(filename):
            return sqlite.connect(filename)
        db = sqlite.connect(filename)
        c = db.cursor()
        c.execute("""CREATE TABLE category (
                id INTEGER PRIMARY KEY,
                label VARCHAR(512) NOT NULL
                )""")
        c.execute("""CREATE TABLE question (
                id INTEGER PRIMARY KEY,
                category INTEGER NOT NULL,
                label VARCHAR(512) NOT NULL,
                mask VARCHAR(500) NOT NULL,
                created TIMESTAMP NOT NULL
                )""")
        c.execute("""CREATE TABLE answers (
                id INTEGER PRIMARY KEY,
                question INTEGER NOT NULL,
                label VARCHAR(512) NOT NULL,
                good VARCHAR(1) NOT NULL
                )""")
        c.execute("""CREATE TABLE score (
                id INTEGER PRIMARY KEY,
                win INTEGER,
                lost INTEGER,
                mask VARCHAR(512) NOT NULL
                )""")
        db.commit()
        return db

    def contribstats (self,irc,msg,args,channel,text):
        """[<channel>] <category> return the best 5 contributors, in category if filled"""
        db = self.getDb(channel)
        c = db.cursor()
        if text:
            c.execute("""SELECT id, mask FROM question WHERE category=%s""",text)
        else:
            c.execute("""SELECT id, mask FROM question""")
        if c.rowcount:
            qs = c.fetchall()
            d = {} 
            for q in qs:
                (id,mask) = q
                if not mask in d:
                    d[mask] = 1
                else:
                    d[mask] = d[mask]+1
            L = []
            for m in d:
                L.append([m,d[m]])
            def sort_function (item):
                return item[1]
            L.sort(key=sort_function)
            L.reverse()
            n = 1
            irc.queueMsg(ircmsgs.privmsg(msg.nick,'Best contributors:'))
            for i in L:
                if n < 6:
                    irc.queueMsg(ircmsgs.privmsg(msg.nick,'%s - %s ( %s )' % (n,i[0].split('!')[0],i[1])))
                n = n+1 
        else:
            irc.reply("no stats available")
    contribstats = wrap (contribstats,['channel',optional('text')])

    def qcontrib (self,irc,msg,args,channel,text):
        """[<channel>] <nick|hostmask> return contributor place"""
        db = self.getDb(channel)
        c = db.cursor()
        if text:
            if text in irc.state.channels[channel].users:
                hostmask = irc.state.nickToHostmask(text)
            else:
                hostmask = text
        else:
            hostmask = msg.prefix
        c.execute("""SELECT id, mask FROM question""")
        if c.rowcount:
            qs = c.fetchall()
            d = {} 
            for q in qs:
                (id,mask) = q
                if not mask in d:
                    d[mask] = 1
                else:  
                    d[mask] = d[mask]+1
            L = []
            for m in d:
                L.append([m,d[m]])   
            def sort_function (item):
                return item[1]
            L.sort(key=sort_function) 
            L.reverse()
            n = 1 
            for i in L:  
                if i[0] == hostmask:
                    irc.reply('%s ( %s questions )' % (n,i[1])) 
                    return
                n = n+1 
            irc.reply('not contribution found')
        else:
            irc.reply("no stats available")
    qcontrib = wrap (qcontrib,['channel',optional('text')])

    def quizzstats (self,irc,msg,args,channel):
        """[<channel>] return the best 5 players ordered by wins/losts ratio"""
        db = self.getDb(channel)
        c = db.cursor()
        c.execute("""SELECT mask, win, lost FROM score""")
        if c.rowcount:
            users = c.fetchall()
            L = []
            percent = 1.00
            d = {}
            for user in users:
                (mask,win,lost) = user
                if win == 0:
                    win = percent
                else:
                    win = win*percent
                if lost == 0:
                    lost = percent
                else:
                    lost = lost*percent
                a = [mask.split('!')[0],win,lost,win/lost]
                a[3] = int(a[3]*10)
                if not mask in d:
                    L.append(a)
                    d[mask] = a
            def sort_inner(inner):
                return inner[3]*inner[1]
            L.sort(key=sort_inner)           
            L.reverse()
            n = 1
            irc.queueMsg(ircmsgs.privmsg(msg.nick,'Quizz stats of %s:' % channel))
            for u in L:
                if n < 6:
                    irc.queueMsg(ircmsgs.privmsg(msg.nick,'%s - %s [%s win/%s lost]' % (n,u[0].split('!')[0],u[1],u[2])))
                n = n+1
        else:
            irc.reply('no stats found')
    quizzstats = wrap(quizzstats,['channel'])

    def quizzscore (self,irc,msg,args,channel,text):
        """[<channel>] <nick|hostmask> return quizz stats"""
        if text:
            if text in irc.state.channels[channel].users:
                hostmask = irc.state.nickToHostmask(text)
            else:
                hostmask = text
        else:
            hostmask = msg.prefix
        db = self.getDb(channel)
        c = db.cursor()
        c.execute("""SELECT mask, win, lost FROM score""")
        if c.rowcount:
            users = c.fetchall()
            L = []
            percent = 1.00
            d = {}
            for user in users:
                (mask,win,lost) = user
                if win == 0:
                    win = percent
                else:
                    win = win*percent
                if lost == 0:
                    lost = percent
                else:
                    lost = lost*percent
                a = [mask,win,lost,win/lost]
                a[3] = int(a[3]*10)
                if not mask in d:
                    L.append(a)
                    d[mask] = a
            def sort_inner(inner):
                return inner[3]*inner[1]
            L.sort(key=sort_inner)           
            L.reverse()
            n = 1
            for u in L:
                if hostmask == u[0]:
                    irc.reply('%s [%s win/%s lost]' % (n,u[1],u[2]))
                    return                     
                n = n+1
        else:
            irc.reply('no stats found')
    quizzscore = wrap(quizzscore,['channel',optional('text')])
    
    def qac (self,irc,msg,args,channel,text):
        """[<channel>] [<category>] add a new category"""
        db = self.getDb(channel)
        c = db.cursor()
        try:
            c.execute("""SELECT id, label FROM category WHERE label=%s LIMIT 1""",text)
            if c.rowcount:
               (id,label) = c.fetchall()[0]
               irc.reply("there is a category #%s named '%s'" % (id,label))
               return
            c.execute("""INSERT INTO category VALUES (NULL, %s)""", text)
            db.commit()
            id = db.insert_id()
            irc.reply("Category #%s '%s' added" % (id,text))
        except:
            irc.reply('database locked, try again later')
            return
    qac = wrap(qac,['owner','channel','text'])
 
    def qdc (self,irc,msg,args,channel,text):
        """[<channel>] [<id>] remove a category"""
        db = self.getDb(channel)
        c = db.cursor()
        try:
            c.execute("""SELECT id, label FROM category WHERE id=%s""",text)
            if c.rowcount:
                (id,label) = c.fetchall()[0]
                c.execute("""DELETE FROM category WHERE id=%s""",id)
                c.execute("""SELECT id, label FROM question WHERE category=%s""",id) 
                n = c.rowcount 
                if c.rowcount:
                    questions = c.fetchall()
                    for q in questions:
                        (qid,qlabel) = q
                        c.execute("""DELETE FROM answers WHERE question=%s""",qid)
                    c.execute("""DELETE FROM question WHERE category=%s""",id)
                db.commit()
                irc.reply("Category #%s '%s' deleted, %s questions too" % (id,label,n))
            else:
                irc.reply('Cannot found category #%s')
        except:
            irc.reply('database locked, try again later')
    qdc = wrap(qdc,['owner','channel','int'])
   
    def qlc (self,irc,msg,args,channel):
        """[<channel>] list category"""
        db = self.getDb(channel)
        c = db.cursor()
        try:
            c.execute("""SELECT id, label FROM category""")
            if c.rowcount:
                n = c.rowcount
                cats = c.fetchall()
                irc.queueMsg(ircmsgs.privmsg(msg.nick,'%s category found:' % n))
                for cat in cats:
                    (id,label) = cat
                    c.execute("""SELECT id, label FROM question WHERE category=%s""",id)
                    irc.queueMsg(ircmsgs.privmsg(msg.nick,'[#%s %s] %s questions' % (cat[0],cat[1],c.rowcount)))
            else:
                irc.reply('no category found')
        except:
            irc.reply('database locked, try again later')
    qlc = wrap(qlc,['owner','channel'])

    def qec (self,irc,msg,args,channel,id,text):
        """[<channel>] [<id>] [<text>] edit a category"""
        db = self.getDb(channel)
        c = db.cursor()
        try:
            c.execute("""SELECT id, label FROM category WHERE id=%s""",id)
            if c.rowcount:
                (id,label) = c.fetchall()[0]
                c.execute("""UPDATE category SET label=%s WHERE id=%s""",(text,id))
                db.commit()
                irc.reply("category #%s updated: '%s' --> '%s'" % (id,label,text))
            else:
                irc.reply('no answer found')
        except:
            irc.reply('database locked, try again later')
    qec = wrap(qec,['owner','channel','int','text'])


    def qlq (self,irc,msg,args,channel,text):
        """[<channel>] <category> list question, filtered by category if filled"""
        db = self.getDb(channel)
        c = db.cursor()
        try:
            if text and text.isdigit():
                c.execute("""SELECT id, label, mask, category FROM question WHERE category=%s""",int(text))
            else:
                c.execute("""SELECT id, label, mask, category FROM question""")
            if c.rowcount:
                qs = c.fetchall()
                for q in qs:
                    irc.queueMsg(ircmsgs.privmsg(msg.nick,'[#%s by %s in %s: %s]' % (q[0],q[2].split('!')[0],q[3],q[1])))
            else:
                irc.reply('no question found')
        except:
            irc.reply('database locked, try again later')
    qlq = wrap(qlq,['owner','channel',optional('text')])

    def qmq (self,irc,msg,args,channel,id,target):
        """[<channel>] [<id>] [<target>] move question to category"""
        db = self.getDb(channel)
        c = db.cursor()
        try:
           c.execute("""SELECT id, label FROM question WHERE id=%s""",id)
           if c.rowcount:
               (id,label) = c.fetchall()[0]
               c.execute("""SELECT id, label FROM category WHERE id=%s""",target)
               if c.rowcount:
                   (catid,catlabel) = c.fetchall()[0]
                   c.execute("""UPDATE question SET category=%s WHERE id=%s""",(target,id))
                   db.commit()
                   irc.reply("Question #%s '%s' moved to '%s'" % (id,label,catlabel))
               else:
                   irc.reply('cannot found targeted category')   
           else:
               irc.reply('no question found')
        except:
             irc.reply('database locked, try again later')
    qmq = wrap(qmq,['owner','channel','int','int'])

    def qeq (self,irc,msg,args,channel,id,text):
        """[<channel>] [<id>] [<text>] edit a question"""
        db = self.getDb(channel)
        c = db.cursor()
        try:
            c.execute("""SELECT id, label FROM question WHERE id=%s""",id)
            if c.rowcount:
                (id,label) = c.fetchall()[0]
                c.execute("""UPDATE question SET label=%s WHERE id=%s""",(text,id))
                db.commit()
                irc.reply("question #%s updated: '%s' --> '%s'" % (id,label,text))
            else:
                irc.reply('no answer found')
        except:
            irc.reply('database locked, try again later')
    qeq = wrap(qeq,['owner','channel','int','text'])

    def qdq (self,irc,msg,args,channel,text):
        """[<channel>] [<id>] delete a question"""
        db = self.getDb(channel)
        c = db.cursor()
        try:
            c.execute("""SELECT id,label FROM question WHERE id=%s""",text)
            if c.rowcount:
                (id,label) = c.fetchall()[0]
                c.execute("""DELETE FROM answers WHERE question=%s""",id)
                c.execute("""DELETE FROM question WHERE id=%s""",id)
                db.commit()
                irc.reply("question #%s '%s' deleted" % (id,label))                
            else:
                irc.reply("there is no question #%s" % text)
        except:
            irc.reply('database locked, try again later')
    qdq = wrap(qdq,['owner','channel','int'])
    
    def qla (self,irc,msg,args,channel,text):
        """[<channel>] <question> list answer, filtered by a question if filled"""
        db = self.getDb(channel)
        c = db.cursor()
        try:
            if text:
                c.execute("""SELECT id, question, label, good FROM answers WHERE question=%s""",text)
            else:
                c.execute("""SELECT id, question, label, good FROM answers""")
            if c.rowcount:
                as = c.fetchall()
                for a in as:
                    irc.queueMsg(ircmsgs.privmsg(msg.nick,"[#%s (question #%s) '%s' %s]" % (a[0],a[1],a[2],a[3])))
            else:
                irc.reply('no answers found')
        except:
            irc.reply('database locked, try again later')
    qla = wrap(qla,['owner','channel',optional('text')])

    def qea (self,irc,msg,args,channel,id,text):
        """[<channel>] [<id>] [<text>] edit an answer"""
        db = self.getDb(channel)
        c = db.cursor()
        try:
            c.execute("""SELECT id, label FROM answers WHERE id=%s""",id)
            if c.rowcount:
                (id,label) = c.fetchall()[0]
                c.execute("""UPDATE answers SET label=%s WHERE id=%s""",(text,id))
                db.commit()
                irc.reply("answer #%s updated: '%s' --> '%s'" % (id,label,text))
            else:
                irc.reply('no answer found')
        except:
            irc.reply('database locked, try again later')
    qea = wrap(qea,['owner','channel','int','text'])

    def qda (self,irc,msg,args,channel,text):
        """[<channel>] [<id>] delete an answer"""
        db = self.getDb(channel)
        c = db.cursor()
        try:
            c.execute("""SELECT id,label FROM answer WHERE id=%s""",text)
            if c.rowcount:
                (id,label) = c.fetchall()[0]
                c.execute("""DELETE FROM answers WHERE id=%s""",id)
                db.commit()
                irc.reply("answer #%s '%s' deleted" % (id,label))                
            else:
                irc.reply("there is no answer #%s" % text)
        except:
            irc.reply('database locked, try again later')
    qda = wrap(qda,['owner','channel','int'])

    def quizzadd (self,irc,msg,args,channel):
        """[<channel>] add a quizz"""
        db = self.getDb(channel)
        c = db.cursor()
        try:
            c.execute("""SELECT id, label FROM category""")
        except:
            irc.reply('database locked, try again later')
            return
        if not c.rowcount:
            irc.queueMsg(ircmsgs.privmsg(msg.nick,"there is no category defined for this channel"))
            return
        if not msg.prefix in self.pending:
            s = Session ()
            s.hostmask = msg.prefix
            s.channel = channel
            self.pending[msg.prefix] = s
            irc.queueMsg(ircmsgs.privmsg(msg.nick,"Welcome to the Quizz Wizard for %s, you can cancel it at anytime by typing 'abort'" % channel))
            if c.rowcount == 1:
                cat = c.fetchall()[0]
                s.category = cat[0]
                irc.queueMsg(ircmsgs.privmsg(msg.nick,"Category #%s '%s' selected" % (cat[0],cat[1])))
                irc.queueMsg(ircmsgs.privmsg(msg.nick,"Type the question of this quizz"))
            else:
                cats = c.fetchall()
                irc.queueMsg(ircmsgs.privmsg(msg.nick,"Select a category, enter the id:"))
                if len(cats) < 10:
                    L = []
                    for cat in cats:
                        L.append('[%s - %s]' % (cat[0],cat[1]))
                    irc.queueMsg(ircmsgs.privmsg(msg.nick,' '.join(L)))
                else:
                    for cat in cats:
                        irc.queueMsg(ircmsgs.privmsg(msg.nick,'%s - %s' % (cat[0],cat[1])))
        else:
            irc.queueMsg(ircmsgs.privmsg(msg.nick,"you still have a quizz wizard active, use 'abort' if you want to stop it"))
            return
    quizzadd = wrap(quizzadd,['channel'])
    
    def doPrivmsg(self,irc,msg):
        if msg.nick == irc.nick:
            return
        (channels, text) = msg.args
        text = text.lstrip().rstrip()
        for channel in channels.split(','):
            if not irc.isChannel(channel) and msg.prefix in self.pending:
                s = self.pending[msg.prefix]
                if text == 'abort':
                    irc.queueMsg(ircmsgs.privmsg(msg.nick,'Quizz wizard aborted'))
                    del self.pending[msg.prefix]
                    return
                if not s.category:
                    db = self.getDb(s.channel)
                    c = db.cursor()
                    try:
                        c.execute("""SELECT id, label FROM category WHERE id=%s""",text)
                        if c.rowcount:
                            matchs = c.fetchall()
                            (id,label) = matchs[0]
                            s.category = id
                            irc.queueMsg(ircmsgs.privmsg(msg.nick,'Category selected : %s - %s' % (id,label)))
                            irc.queueMsg(ircmsgs.privmsg(msg.nick,"Type the question of this quizz"))
                            return
                        else:
                          irc.queueMsg(ircmsgs.privmsg(msg.nick,'Invalid id given, try again'))
                          return 
                    except:
                        irc.queueMsg(ircmsgs.privmsg(msg.nick,'Quizz wizard aborted'))
                        del self.pending[msg.prefix]
                        return
                if not s.question:
                    s.question = text
                    irc.queueMsg(ircmsgs.privmsg(msg.nick,'Ok, now provide answers, one per line, if you give only one (the right answer), you must use \"/regexp/i\", when you have finish, type \'done\''))
                    return
                if not s.answered:
                    if text != 'done':
                        s.answers.append(text)
                        irc.queueMsg(ircmsgs.privmsg(msg.nick,"Answer #%s added" % (len(s.answers)-1)))
                    else:
                        if not len(s.answers):
                           irc.queueMsg(ircmsgs.privmsg(msg.nick,'You need at least provide one answer'))
                           return
                        if len(s.answers) == 1:
                            try:
                                reg = utils.str.perlReToPythonRe(s.answers[0])
                                try:
                                    db = self.getDb(s.channel)
                                    c = db.cursor()
                                    c.execute("""INSERT INTO question VALUES (NULL, %s, %s, %s, %s)""", s.category, s.question, s.hostmask, int(time.time()))
                                    id = db.insert_id()
                                    c.execute("""INSERT INTO answers VALUES (NULL, %s, %s, %s)""", id, s.answers[0],'1')
                                    db.commit()
                                    irc.queueMsg(ircmsgs.privmsg(msg.nick,"Quizz #%s created, thank you" % id))
                                    del self.pending[msg.prefix]
                                    return
                                except:
                                    irc.queueMsg(ircmsgs.privmsg(msg.nick,"database locked, quizz wizard aborted"))
                                    del self.pending[msg.prefix]
                                    return
                            except:
                                irc.queueMsg(ircmsgs.privmsg(msg.nick,"bad regular expression given, quizz wizard aborted, take a look here : http://www.cs.tut.fi/~jkorpela/perl/regexp.html"))
                                del self.pending[msg.prefix]
                                return
                        else:
                            s.answered = True
                            irc.queueMsg(ircmsgs.privmsg(msg.nick,"Tell me which answers are right, by typing answer's id, when you have finish, type 'done'"))
                    return
                if text == 'done':
                    if len(s.valid) == 0:
                        irc.queueMsg(ircmsgs.privmsg(msg.nick,"Tell me which answers are right, by typing answer's id, when you have finish, type 'done'"))
                        return
                    else:
                        try:
                            db = self.getDb(s.channel)
                            c = db.cursor()
                            c.execute("""INSERT INTO question VALUES (NULL, %s, %s, %s, %s)""", s.category, s.question, s.hostmask, int(time.time()))
                            id = db.insert_id()
                            n = 0
                            for a in s.answers:
                                g = '0'
                                for x in s.valid:
                                    if x == n:
                                        g = '1'
                                        break
                                c.execute("""INSERT INTO answers VALUES (NULL, %s, %s, %s)""", id,a,g)
                                n = n+1
                            db.commit()
                            irc.queueMsg(ircmsgs.privmsg(msg.nick,'Quizz #%s created, thank you' % id))
                            del self.pending[msg.prefix]
                            return
                        except:
                            irc.queueMsg(ircmsgs.privmsg(msg.nick,'database locked, quizz wizard aborted'))
                            del self.pending[msg.prefix]
                            return
                if text.isdigit():
                    n = int(text)
                    if n < len(s.answers):
                       s.valid.append(n)
                       irc.queueMsg(ircmsgs.privmsg(msg.nick,"#%s '%s' is now a right answer" % (n,s.answers[n])))
                    else:
                       irc.queueMsg(ircmsgs.privmsg(msg.nick,'Invalid id given'))
                else:
                    irc.queueMsg(ircmsgs.privmsg(msg.nick,'Invalid id given'))

            if irc.isChannel(channel) and channel in self.game and not msg.nick == irc.nick:
               g = self.game[channel]
               if g == True:
                   return
               if g.owner == msg.prefix or g.end:
                   return
               good = False
               if g.good:
                   if g.good.search(text):
                       good = True   
               else:
                   for a in g.answers:
                       (id,label,k) = a
                       if k == '1' or k == 1:
                           if text.isdigit():
                              if id == int(text):
                                 good = True
               if good and not msg.prefix in g.players or good and g.good:
                   g.end = True
                   del self.game[channel]
                   self.game[channel] = True
                   def ugame ():
                       if channel in self.game:
                           del self.game[channel]
                   schedule.addEvent(ugame,time.time()+self.registryValue('delay',channel=channel))
                   irc.sendMsg(ircmsgs.privmsg(channel,'%s wins.' % msg.nick))
                   try:
                       db = self.getDb(channel)
                       c = db.cursor()
                       c.execute("""SELECT id, win, lost FROM score WHERE mask=%s LIMIT 1""",msg.prefix)
                       if c.rowcount:
                           (id,win,lost) = c.fetchall()[0]
                           w = win+1
                           c.execute("""UPDATE score SET win=%s WHERE id=%s""",(w,id))
                       else:
                           c.execute("""INSERT INTO score VALUES (NULL,1,0,%s)""",msg.prefix)
                       for m in g.players:
                           if not m == msg.prefix:
                               c.execute("""SELECT id, win, lost FROM score WHERE mask=%s LIMIT 1""",m)
                               if c.rowcount:
                                   (id,win,lost) = c.fetchall()[0]
                                   l = lost+1
                                   c.execute("""UPDATE score SET lost=%s WHERE id=%s""",(l,id))
                               else:
                                   c.execute("""INSERT INTO score VALUES (NULL,0,1,%s)""",m)
                       db.commit()
                       return
                   except:
                       self.log.info('error in Quizz win')
               else:
                   if len(g.answers) != 1 and text.isdigit():
                       for a in g.answers:
                           (id,label,k) = a
                           if id == int(text):
                               g.players[msg.prefix] = text
                               break
                   elif g.good:
                      g.players[msg.prefix] = text 
 
    def quizz (self,irc,msg,args,channel,text):
        if channel in self.game:
            if self.game[channel] == True:
                irc.reply('patience')
            else:
                irc.reply('there is still an active quizz')
            return
        g = Game()
        g.channel = channel
        db = self.getDb(channel)
        c = db.cursor()
        if not text:
           c.execute("""SELECT id, category, label, mask FROM question""")
        else:
           c.execute("""SELECT id, label FROM category WHERE id=%s LIMIT 1""",text)
           if not c.rowcount:
              c.execute("""SELECT id, label FROM category WHERE label LIKE %s LIMIT 1""",text)
           if c.rowcount:
              (id,label) = c.fetchall()[0]
              c.execute("""SELECT id, category, label, mask FROM question WHERE category=%s""",id)
           else:
              c.execute("""SELECT id, category, label, mask FROM question""")
 
        if c.rowcount:
           if c.rowcount == 1:
              question = c.fetchall()[0]
           else:
              question = random.choice(c.fetchall())

           g.question = question[0]
           g.owner = question[3]
           c.execute("""SELECT id, label FROM category WHERE id=%s""",question[1])
           if c.rowcount:
               category = c.fetchall()[0]
               g.category = category[1]
               c.execute("""SELECT id, label, good FROM answers WHERE question=%s""",g.question)
               def ub():
                   if channel in self.game:
                       if self.game[channel] == g:
                           co = db.cursor()
                           for m in g.players:
                               co.execute("""SELECT id, win, lost FROM score WHERE mask=%s LIMIT 1""",m)
                               if co.rowcount:
                                   (id,win,lost) = co.fetchall()[0]
                                   l = lost+1
                                   co.execute("""UPDATE score SET lost=%s WHERE id=%s""",(l,id))
                               else:
                                   co.execute("""INSERT INTO score VALUES (NULL,0,1,%s)""",m)
                           db.commit()
                           irc.queueMsg(ircmsgs.privmsg(channel,'Quizz finished.'))
                           del self.game[channel]
               
               if c.rowcount:
                   self.game[channel] = g
                   if c.rowcount == 1:
                      go = c.fetchall()[0] 
                      reg = go[1]
                      if reg.startswith('"'):
                         reg = reg.replace('"','',1)
                      if reg.endswith('"'):
                         reg = reg.replace('"','',len(reg))
                      self.log.info('compiling #%s : %s' % (go,reg))                             
                      g.good = utils.str.perlReToPythonRe(reg)
                      irc.queueMsg(ircmsgs.privmsg(channel,'[#%s by %s in %s] %s' % (g.question,g.owner.split('!')[0],g.category,question[2])))
                      irc.queueMsg(ircmsgs.privmsg(channel,'mode: find the answer'))
                      schedule.addEvent(ub,time.time()+self.registryValue('duration',channel=g.channel))                           
                   else:
                      answers = c.fetchall()
                      g.answers = []
                      irc.queueMsg(ircmsgs.privmsg(channel,'[#%s by %s in %s] %s' % (g.question,g.owner.split('!')[0],g.category,question[2])))
                      irc.queueMsg(ircmsgs.privmsg(channel,'mode: one answer per user'))
                      n = 0
                      r = []
                      for a in answers:
                          r.append([a[0],a[1],a[2]])
                      random.shuffle(r)
                      for a in r:
                         a[0] = n
                         g.answers.append(a)
                         n = n+1
                      for a in g.answers:
                          irc.queueMsg(ircmsgs.privmsg(channel,'%s - %s' % (a[0],a[1])))
                      schedule.addEvent(ub,time.time()+self.registryValue('duration',channel=g.channel))
    quizz = wrap(quizz, ['channel',optional('text')])
    
Class = Quizz


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
