from urllib2 import urlopen
import simplejson as json

class Reddit:
    def __init__(self):
        self.url = 'http://www.reddit.com/user/%s/about.json'
    def karma(self, phrase):
        user = phrase
        raw = urlopen(self.url % phrase).read()
        data = json.loads(raw)['data']
        karma = data['link_karma']
        comment = data['comment_karma']
        return (user, karma, comment, user)

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        phrase = sys.argv[1]
        reddit = Reddit()
        values = reddit.karma(phrase)
        print "%s has %s link karma and %s comment karma: http://www.reddit.com/user/%s" % (values)
