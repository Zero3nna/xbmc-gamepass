import urllib
import urllib2
import re
import os
import json
import cookielib
import time
import xbmcplugin
import xbmcgui
import xbmcvfs
import xbmcaddon
import StorageServer
import xml.etree.ElementTree as ET
import random
import md5
from uuid import getnode as get_mac
from datetime import datetime, timedelta
from traceback import format_exc
from urlparse import urlparse, parse_qs
from BeautifulSoup import BeautifulSoup
from BeautifulSoup import BeautifulStoneSoup

addon = xbmcaddon.Addon(id='script.nfl.gamepass')
addon_path = xbmc.translatePath(addon.getAddonInfo('path'))
addon_profile = xbmc.translatePath(addon.getAddonInfo('profile'))
cookie_file = os.path.join(addon_profile, 'cookie_file')
cookie_jar = cookielib.LWPCookieJar(cookie_file)
icon = os.path.join(addon_path, 'icon.png')
fanart = os.path.join(addon_path, 'fanart.jpg')
base_url = ''
debug = addon.getSetting('debug')
addon_version = addon.getAddonInfo('version')
cache = StorageServer.StorageServer("nfl_game_pass", 24)
username = addon.getSetting('email')
password = addon.getSetting('password')


def addon_log(string):
    if debug == 'true':
        xbmc.log("[addon.nfl.gamepass-%s]: %s" %(addon_version, string))

def get_current_week():
    a_week = timedelta(days=7)
    try:
        week_1 = datetime.strptime('August 1 2013', '%B %d %Y')
    except TypeError: # bug in xbmc's python
        week_1 = datetime.fromtimestamp(time.mktime(time.strptime('August 1 2013', '%B %d %Y')))
    for i in range(27):
        if datetime.today() > week_1 + (i * a_week):
            week_index = i
    return week_index

def cache_seasons_and_weeks(login_data):
    soup = BeautifulSoup(login_data, convertEntities=BeautifulSoup.HTML_ENTITIES)

    try:
        seasons_soup = soup.find('select', id='seasonSelect').findChildren()
        seasons = []
        for season in seasons_soup:
            seasons.append(season.string)
        cache.set('seasons', repr(seasons))
        addon_log('Seasons cached')
    except:
        addon_log('Season cache failed')
        return False

    try:
        weeks_soup = soup.find('select', id='weekSelect').findChildren()
        weeks = {}
        index = 0
        for i in weeks_soup:
            weeks[index] = {'week_id': i['value'], 'name': i.string, 'index': index}
            index += 1
        cache.set('weeks', repr(weeks))
        addon_log('Weeks cached')
    except:
        addon_log('Week cache failed')
        return False

    return True


def display_seasons(seasons):
    for season in seasons:
        add_dir(season, season, 2, icon)

def display_weeks(season, weeks):
    for week_code, week_name in sorted(weeks.iteritems()):
        add_dir(week_name, season + ';' + week_code, 3, icon)

def gamepass_login():
    url = 'https://id.s.nfl.com/login'
    post_data = {
        'username': username,
        'password': password,
        'vendor_id': 'nflptnrnln',
        'error_url': 'https://gamepass.nfl.com/nflgp/secure/login?redirect=loginform&redirectnosub=packages&redirectsub=schedule',
        'success_url': 'https://gamepass.nfl.com/nflgp/secure/login?redirect=loginform&redirectnosub=packages&redirectsub=schedule'
    }
    login_data = make_request(url, urllib.urlencode(post_data))

    cache_success = cache_seasons_and_weeks(login_data)

    if cache_success:
        addon_log('login success')
        return True
    else: # if cache failed, then login failed or the login page's HTML changed
        dialog = xbmcgui.Dialog()
        dialog.ok("Login Failed", "Logging into NFL Game Pass failed. Make sure your account information is correct.")
        addon_log('login failed')
        return False

# The plid parameter used when requesting the video path appears to be an MD5 of... something.
# However, I don't knwo what it is an "id" of, since the value seems to change constantly.
# Reusing a plid doesn't work, so, I assume it's a unique identifier for the player as we request a stream.
# This, pseudorandom approach seems to work for now.
def gen_plid():
    rand = random.getrandbits(10)
    mac_address = str(get_mac())
    m = md5.new(str(rand) + mac_address)
    return m.hexdigest()

# the XML manifest of all available streams for a game
def get_manifest(video_path):
    url, port, path = video_path.partition(':443')
    path = path.replace('?', '&')
    url = url.replace('adaptive://', 'http://') + port + '/play?' + urllib.quote_plus('url=' + path, ':&=')

    manifest_data = make_request(url)

    return manifest_data

def get_stream_url(game_id):
    video_path = get_video_path(game_id)
    manifest = get_manifest(video_path)
    stream_url = parse_manifest(manifest)
    return stream_url

# the "video path" provides the info neccesary to request the stream's manifest
def get_video_path(game_id):
    url = 'https://gamepass.nfl.com/nflgp/servlets/encryptvideopath'
    plid = gen_plid()
    post_data = {
        'path': game_id,
        'plid': plid,
        'type': 'fgpa',
        'isFlex': 'true'
    }
    video_path_data = make_request(url, urllib.urlencode(post_data))

    try:
        soup = BeautifulStoneSoup(video_path_data, convertEntities=BeautifulSoup.XML_ENTITIES)
        video_path = soup.find('path')
        addon_log('Video Path Acquired Successfully.')
        return video_path.string
    except:
        addon_log('Video Path Acquisition Failed.')
        return False

# season is in format: YYYY
# week is in format 101 (1st week preseason) or 213 (13th week of regular season)
def get_weeks_games(season=2013, week=103):
    url = 'https://gamepass.nfl.com/nflgp/servlets/games'
    post_data = {
        'isFlex': 'true',
        'season': season,
        'week': week
    }
    game_data = make_request(url, urllib.urlencode(post_data))

    soup = BeautifulStoneSoup(game_data, convertEntities=BeautifulSoup.XML_ENTITIES)
    games = []
    for game in soup('game'):
        game_id = ''
        try:
            game_id = game.programid.string
        except AttributeError:
            addon_log('No program id: %s' %game)
            format_exc()
            continue

        away_team = game.awayteam('city')[0].string + ' ' + game.awayteam('name')[0].string
        home_team = game.hometeam('city')[0].string + ' ' + game.hometeam('name')[0].string

        try:
            start_time = datetime.fromtimestamp(time.mktime(time.strptime(game.gametimegmt.string, '%Y-%m-%dT%H:%M:%S.000')))
            end_time = datetime.fromtimestamp(time.mktime(time.strptime(game.gameendtimegmt.string, '%Y-%m-%dT%H:%M:%S.000')))
            duration = (end_time - start_time).seconds / 60
        except:
            addon_log(format_exc())
            duration = None

        try:
            game_datetime = datetime.fromtimestamp(time.mktime(time.strptime(game.date.string, '%Y-%m-%dT%H:%M:%S.000')))
            game_date_string = game_datetime.strftime('%A, %b %d %I:%M %p')
        except:
            addon_log(format_exc())
            game_date_string = ''

        try:
            away_score = game.awayteam('score')[0].string
            home_score = game.hometeam('score')[0].string
        except:
            away_score = ''
            home_score = ''
            
        thumb_path = 'https://secure.footprint.net/neulion-e4/nfl/player2013-v2/nflgp/flex/images/teams/%s.png'
        home_thumb = thumb_path %game.hometeam('id')[0].string
        away_thumb = thumb_path %game.awayteam('id')[0].string
        

        game = ({'away_team': {'name': away_team,
                               'thumb': away_thumb,
                               'score': away_score},
                 'home_team': {'name': home_team,
                               'thumb': home_thumb,
                               'score': home_score},
                 'date': game_date_string,
                 'duration': duration,
                 'game_id': game_id})
        games.append(game)

    return games

def make_request(url, data=None, headers=None):
    addon_log('Request URL: %s' %url)
    if headers is None:
        headers = {'User-agent' : 'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:22.0) Gecko/20100101 Firefox/22.0',
                   'Referer' : base_url}
    if not xbmcvfs.exists(cookie_file):
        addon_log('Creating cookie_file!')
        cookie_jar.save()
    cookie_jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
    opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cookie_jar))
    urllib2.install_opener(opener)
    try:
        req = urllib2.Request(url, data, headers)
        response = urllib2.urlopen(req)
        cookie_jar.save(cookie_file, ignore_discard=True, ignore_expires=False)
        data = response.read()
        addon_log(str(response.info()))
        redirect_url = response.geturl()
        response.close()
        if redirect_url != url:
                addon_log('Redirect URL: %s' %redirect_url)
        return data
    except urllib2.URLError, e:
        addon_log('We failed to open "%s".' %url)
        if hasattr(e, 'reason'):
            addon_log('We failed to reach a server.')
            addon_log('Reason: %s' %e.reason)
        if hasattr(e, 'code'):
            addon_log('We failed with error code - %s.' %e.code)

def parse_manifest(manifest):
    try:
        soup = BeautifulStoneSoup(manifest, convertEntities=BeautifulStoneSoup.XML_ENTITIES)
        items = [{'servers': [{'name': x['name'], 'port': x['port']} for x in i('httpserver')],
                  'url': i['url'],
                  'info': '%sx%s Bitrate: %s' %(i.video['height'], i.video['width'], i['bitrate'])}
                 for i in soup('streamdata')]

        dialog = xbmcgui.Dialog()
        ret = dialog.select('Choose a stream', [i['info'] for i in items])

        if ret >= 0:
            addon_log('Selected: %s' %items[ret])
            stream_url = 'http://%s%s' %(items[ret]['servers'][1]['name'], items[ret]['url'])
            addon_log('Stream URL: %s' %stream_url)
            return stream_url
        else: raise
    except:
        addon_log(format_exc())
        return False


# def get_params():
    # p = parse_qs(sys.argv[2][1:])
    # for i in p.keys():
        # p[i] = p[i][0]
    # return p


# if debug == 'true':
cache.dbg = True
# params = get_params()
# addon_log("params: %s" %params)

# try:
    # mode = int(params['mode'])
# except:
    # mode = None
    
class GamepassGUI(xbmcgui.WindowXMLDialog):
    def __init__( self, *args, **kwargs ):
        xbmcgui.WindowXMLDialog.__init__(self)
        self.action_previous_menu = (9, 10, 92, 216, 247, 257, 275, 61467, 61448)
        self.seasons = None
        self.weeks = None
        self.season = '2013'
        self.games = None
        
    def onInit(self):
        self.window = xbmcgui.Window(xbmcgui.getCurrentWindowDialogId())
        addon_log('Window ID: %s' %self.window)
        try:
            self.seasons = eval(cache.get('seasons'))
            self.weeks = eval(cache.get('weeks'))
            print 'We have seasons ---------------------'
        except SyntaxError:
            print('No season cache')
            data = make_request('https://gamepass.nfl.com/nflgp/secure/schedule')
            ok = cache_seasons_and_weeks(data)
            if ok:
                print 'OKKOOKOKOKOKOKOKOKKOKOKOKOKO'
                self.seasons = eval(cache.get('seasons'))
        if self.seasons and self.weeks:
            week_index = get_current_week()
            self.week = self.weeks[week_index]['week_id']
        self.display_games()

    def display_games(self):
        print '--- %s - %s ---' %(self.season, self.week)
        # self.games = get_weeks_games(self.season, self.week)
        self.games = cache.cacheFunction(get_weeks_games)
        print '==============================================='
        print self.games
        print len(self.games)
        if self.games:
            index = 0
            for i in self.games:
                index += 1
                control = self.window.getControl(1270 + index)
                listitem = xbmcgui.ListItem(i['away_team']['name'], i['home_team']['name'])
                listitem.setProperty("awayThumb", i['away_team']['thumb'])
                listitem.setProperty("homeThumb", i['home_team']['thumb'])
                listitem.setProperty("game_id", i['game_id'])
                listitem.setProperty('dateString', i['date'])
                listitem.setInfo(type="Video", infoLabels={"Title": '%s at %s' %(i['away_team']['name'], i['away_team']['name']), 
                                                           "Duration": i['duration']})
                control.addItem(listitem)
                if index == 4: break
        else:
            dialog = xbmcgui.Dialog()
            dialog.ok("Fetching Games Failed", "Fetching Game Data Failed.")
            addon_log('Fetching games failed.')
            
        
    def onAction(self, action):
        if action == 13:
            #keyboard x key
            self.close()

        if action in self.action_previous_menu:
            addon_log('Action: action_previous_menu')
            self.close()
            
    
if not xbmcvfs.exists(addon_profile):
    xbmcvfs.mkdir(addon_profile)
    
if (__name__ == "__main__"):
    addon_log('script starting')
    window = GamepassGUI('script-gamepass.xml', addon_path)
    window.doModal()

addon_log('script finished')