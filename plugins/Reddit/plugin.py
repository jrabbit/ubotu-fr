###
# Copyright (c) 2010, jrabbit
# GPL v3 and all later versions.
#
#
###

import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks

from local.reddit import Reddit as Reddit2

class Reddit(callbacks.Plugin):
    """Look up user karma from Reddit.com."""
    threaded = True

    def __init__(self, irc):
        self.__parent = super(Reddit, self)
        self.__parent.__init__(irc)
        #self.dict = urbandictionary.Dictionary()
    def karma(self, irc, msg, args, index, phrase):
        irc.reply("%s has %s link karma and %s comment karma: http://www.reddit.com/user/%s" % Reddit2.karma(phrase))
    karma = wrap(karma, ['text'])

Class = Reddit


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
