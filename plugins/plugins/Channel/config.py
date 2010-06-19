###
# Copyright (c) 2004-2005, Jeremiah Fincher
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


import supybot.conf as conf
import supybot.utils as utils
import supybot.registry as registry

def configure(advanced):
    # This will be called by supybot to configure this module.  advanced is
    # a bool that specifies whether the user identified himself as an advanced
    # user or not.  You should effect your configuration by manipulating the
    # registry as appropriate.
    from supybot.questions import expect, anything, something, yn
    conf.registerPlugin('Channel', True)

Channel = conf.registerPlugin('Channel')

conf.registerGlobalValue(Channel, 'banDatabase',
    registry.String('data/bans.db',"""database path"""))

# channels configuration

conf.registerChannelValue(Channel, 'logChannel',
    registry.String('', """forward some alerts and operator actions to the given channel"""))

conf.registerChannelValue(Channel, 'logSize',
    registry.PositiveInteger(20, """number of lines to store for log"""))

conf.registerChannelValue(Channel, 'warnLife',
    registry.PositiveInteger(300, """interval between warn count cleanup"""))
    
conf.registerChannelValue(Channel, 'opChannel',
    registry.String('', """forward some alerts and information to the given channel"""))

conf.registerChannelValue(Channel, 'evadeBanCheck',
    registry.Boolean(False, """check ban evader on join"""))

conf.registerChannelValue(Channel, 'evadeKickMessage',
    registry.String("Ban evader, discuss with an operator : /msg chanserv access #channel list to find one.", 
    """message used on kban"""))

conf.registerChannelValue(Channel, 'evadeBanDuration',
    registry.NonNegativeInteger(0, 
    """ban duration, in seconds, 0 means forever"""))

conf.registerChannelValue(Channel, 'floodCheck',
    registry.Boolean(False, """enable flood detection"""))

conf.registerChannelValue(Channel, 'floodPermit',
    registry.PositiveInteger(4, 
    """number of message permit during floodMax"""))

conf.registerChannelValue(Channel, 'floodLife',
    registry.PositiveInteger(7, """life cycle for flood detection in seconds"""))

conf.registerChannelValue(Channel, 'floodQuietDuration',
    registry.PositiveInteger(60, """in seconds"""))

conf.registerChannelValue(Channel, 'lowFloodCheck',
    registry.Boolean(False, """enable low flood detection"""))

conf.registerChannelValue(Channel, 'lowFloodPermit',
    registry.PositiveInteger(6, 
    """number of message permit during lowFloodLife"""))

conf.registerChannelValue(Channel, 'lowFloodLife',
    registry.PositiveInteger(13, """life cycle for low flood detection in seconds"""))

conf.registerChannelValue(Channel, 'lowFloodQuietDuration',
    registry.PositiveInteger(300, """in seconds"""))

conf.registerChannelValue(Channel, 'floodMessage',
    registry.String("Don't flood : use a pastebin to copy / paste.", 
    """message send in the channel, with his nick"""))

conf.registerChannelValue(Channel, 'repeatCheck',
    registry.Boolean(False, """enable repeat detection"""))

conf.registerChannelValue(Channel, 'repeatPermit',
    registry.PositiveInteger(5, """number of message permit during repeatLife"""))

conf.registerChannelValue(Channel, 'repeatLife',
    registry.PositiveInteger(120, """life cycle for repeat detection in seconds"""))

conf.registerChannelValue(Channel, 'repeatMessage',
    registry.String("Don't repeat, be patient, if someone can give you an answer he will do.", 
    """message send in the channel, with his nick"""))

conf.registerChannelValue(Channel, 'repeatQuietDuration',
    registry.PositiveInteger(120, """quiet in seconds"""))

conf.registerChannelValue(Channel, 'highlightCheck',
    registry.Boolean(False, """enable highlight detection"""))

conf.registerChannelValue(Channel, 'highlightPermit',
    registry.NonNegativeInteger(5, """number of highlight user permit in one message"""))

conf.registerChannelValue(Channel, 'highlightMessage',
    registry.String("Don't highlight too much people.", """message send in the channel, with his nick"""))

conf.registerChannelValue(Channel, 'highlightQuietDuration',
    registry.PositiveInteger(120, """quiet duration"""))

conf.registerChannelValue(Channel, 'noticeCheck',
    registry.Boolean(False, """enable notice detection"""))

conf.registerChannelValue(Channel, 'noticeLife',
    registry.PositiveInteger(3, """life cycle for notice detection in seconds"""))

conf.registerChannelValue(Channel, 'noticePermit',
    registry.NonNegativeInteger(0, """number of notice permit during noticeLife"""))
    
conf.registerChannelValue(Channel, 'noticeMessage',
    registry.String("Don't notice or ctcp channel.", """message send in the channel, with his nick"""))
    
conf.registerChannelValue(Channel, 'noticeQuietDuration',
    registry.PositiveInteger(300, """quiet duration in seconds"""))

conf.registerChannelValue(Channel, 'badUserLife',
    registry.PositiveInteger(1800, """life cycle for bad user detection in seconds"""))

conf.registerChannelValue(Channel, 'badUserPermit',
    registry.PositiveInteger(2, """if more than value bad action during badUserLife, apply badUserBan kban"""))

conf.registerChannelValue(Channel, 'cycleCheck',
    registry.Boolean(False, """enable cycle flood detection"""))

conf.registerChannelValue(Channel, 'cyclePermit',
    registry.PositiveInteger(2, """number of part permit during"""))
    
conf.registerChannelValue(Channel, 'cycleLife',
    registry.PositiveInteger(20, """massjoin life cycle duration"""))

conf.registerChannelValue(Channel, 'cycleBanDuration',
    registry.PositiveInteger(3600, """mode duration"""))

conf.registerChannelValue(Channel, 'commandCheck',
    registry.Boolean(False, """enable command abuse detection"""))

conf.registerChannelValue(Channel, 'commandPermit',
    registry.PositiveInteger(4, """number of same command permit during"""))

conf.registerChannelValue(Channel, 'commandLife',
    registry.PositiveInteger(60, """command life cycle duration"""))

conf.registerChannelValue(Channel, 'commandDisableDuration',
    registry.PositiveInteger(300, """command disable for this channel during this time in seconds"""))

conf.registerChannelValue(Channel, 'colorCheck',
    registry.Boolean(False, """enable color abuse detection"""))

conf.registerChannelValue(Channel, 'colorPermit',
    registry.PositiveInteger(4, """number of part permit during"""))

conf.registerChannelValue(Channel, 'colorLife',
    registry.PositiveInteger(20, """massjoin life cycle duration"""))

conf.registerChannelValue(Channel, 'colorDisableDuration',
    registry.PositiveInteger(300, """mode +C duration"""))

conf.registerChannelValue(Channel, 'badUserMessage',
    registry.String("That's enough, bye.", """appears in kban message"""))

conf.registerChannelValue(Channel, 'badUserBanDuration',
    registry.PositiveInteger(7200, """duration of bad user kban"""))

conf.registerChannelValue(Channel, 'massjoinCheck',
    registry.Boolean(False, """enable mass join detection"""))

conf.registerChannelValue(Channel, 'massjoinPermit',
    registry.PositiveInteger(5, """number of join permit during"""))
    
conf.registerChannelValue(Channel, 'massjoinLife',
    registry.PositiveInteger(6, """massjoin life cycle duration"""))

conf.registerChannelValue(Channel, 'massjoinMode',
    registry.String("+r-z+q $~a", """modes to apply"""))

conf.registerChannelValue(Channel, 'massjoinUnMode',
    registry.String("-r+z-q $~a", """modes to remove"""))

conf.registerChannelValue(Channel, 'massjoinDuration',
    registry.PositiveInteger(300, """mode duration"""))

conf.registerChannelValue(Channel, 'alwaysRejoin',
    registry.Boolean(True, """Determines whether the bot will always try to
    rejoin a channel whenever it's kicked from the channel."""))



# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
