import logging
import time
import datetime
import random
import coloredlogs
import sys
import json
import copy
import os
import collections
import traceback

from core.extractors import Extractor
from core.request import WebWrapper
from game.village import Village
from manager import VillageManager

coloredlogs.install(level=logging.DEBUG, fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("selenium").setLevel(logging.ERROR)

os.chdir(os.path.dirname(os.path.realpath(__file__)))


class TWB:
    res = None
    villages = [

    ]
    wrapper = None
    should_run = True
    gd = None
    daemon = "-d" in sys.argv
    runs = 0

    def manual_config(self):
        print("Hello and welcome, it looks like you don't have a config file (yet)")
        if not os.path.exists('config.example.json'):
            print("Oh now, config.example.json and config.json do not exist. You broke something didn't you?")
            return False
        print("Please enter the current (logged-in) URL of the world you are playing on (or q to exit)")
        input_url = input("URL: ")
        if input_url.strip() == "q":
            return False
        server = input_url.split('://')[1].split("/")[0]
        game_endpoint = input_url.split("?")[0]
        sub_parts = server.split(".")[0]
        print("Game endpoint: %s" % game_endpoint)
        print("World: %s" % sub_parts.upper())
        check = input("Does this look correct? [nY]")
        if "y" in check.lower():
            with open('config.example.json', 'r') as template_file:
                template = json.load(template_file, object_pairs_hook=collections.OrderedDict)
                template['server']['endpoint'] = game_endpoint
                template['server']['server'] = sub_parts.lower()
                with open('config.json', 'w') as newcf:
                    json.dump(template, newcf, indent=2, sort_keys=False)
                    print("Deployed new configuration file")
                    return True
        print("Make sure your url starts with https:// and contains the game.php? part")
        return self.manual_config()

    def config(self):
        template = None
        if os.path.exists('config.example.json'):
            with open('config.example.json', 'r') as template_file:
                template = json.load(template_file, object_pairs_hook=collections.OrderedDict)
        if not os.path.exists('config.json'):
            if self.manual_config():
                return self.config()
            else:
                print("Unable to start without a valid config file")
                sys.exit(1)
        config = None
        with open('config.json', 'r') as f:
            config = json.load(f, object_pairs_hook=collections.OrderedDict)
        if template and config['build']['version'] != template['build']['version']:
            print("Outdated config file found, merging (old copy saved as config.bak)\n"
                  "Remove config.example.json to disable this behaviour")
            with open('config.bak', 'w') as backup:
                json.dump(config, backup, indent=2, sort_keys=False)
            config = self.merge_configs(config, template)
            with open('config.json', 'w') as newcf:
                json.dump(config, newcf, indent=2, sort_keys=False)
                print("Deployed new configuration file")
        return config

    def merge_configs(self, old_config, new_config):
        to_ignore = ["villages", "build"]
        for section in old_config:
            if section not in to_ignore:
                for entry in old_config[section]:
                    if entry in new_config[section]:
                        new_config[section][entry] = old_config[section][entry]
        villages = collections.OrderedDict()
        for v in old_config['villages']:
            nc = new_config["village_template"]
            vdata = old_config['villages'][v]
            for entry in nc:
                if entry not in vdata:
                    vdata[entry] = nc[entry]
            villages[v] = vdata
        new_config['villages'] = villages
        return new_config

    def add_village(self, vid, template=None):
        original = self.config()
        with open('config.bak', 'w') as backup:
            json.dump(original, backup, indent=2, sort_keys=False)
        if not template and 'village_template' not in original:
            print("Village entry %s could not be added to the config file!" % vid)
            return
        original['villages'][vid] = template if template else original['village_template']
        with open('config.json', 'w') as newcf:
            json.dump(original, newcf, indent=2, sort_keys=False)
            print("Deployed new configuration file")

    def run(self):
        config = self.config()
        self.wrapper = WebWrapper(config['server']['endpoint'],
                                  server=config['server']['server'],
                                  endpoint=config['server']['endpoint'],
                                  reporter_enabled=config['reporting']['enabled'],
                                  reporter_constr=config['reporting']['connection_string'])

        self.wrapper.start(username="dontcare",
                           password="dontcare", keep_session=True)
        result_villages = None
        if 'add_new_villages' in config['bot'] and config['bot']['add_new_villages']:
            result_villages = self.wrapper.get_url("game.php?screen=overview_villages")
            result_villages = Extractor.village_ids_from_overview(result_villages)
            needs_reset = False
            for found_vid in result_villages:
                if found_vid not in config['villages']:
                    print("Village %s was found but no config entry was found. Adding automatically" % found_vid)
                    self.add_village(vid=found_vid)
                    needs_reset = True
            if needs_reset:
                return self.run()

        for vid in config['villages']:
            v = Village(wrapper=self.wrapper, village_id=vid)
            self.villages.append(copy.deepcopy(v))
        # setup additional builder
        rm = None
        defense_states = {}
        while self.should_run:
            config = self.config()
            vnum = 1
            for vil in self.villages:
                if result_villages and vil.village_id not in result_villages:
                    print("Village %s will be ignored because it is not available anymore" % vil.village_id)
                    continue
                if not rm:
                    rm = vil.rep_man
                else:
                    vil.rep_man = rm
                if 'auto_set_village_names' in config['bot'] and config['bot']['auto_set_village_names']:
                    template = config['bot']['village_name_template']
                    fs = '%0'+str(config['bot']['village_name_number_length'])+'d'
                    num_pad = fs % vnum
                    template = template.replace('{num}', num_pad)
                    vil.village_set_name = template

                vil.run(config=config, first_run=vnum == 1)
                if vil.get_config(section="units", parameter="manage_defence", default=False) and vil.def_man:
                    defense_states[vil.village_id] = vil.def_man.under_attack if vil.def_man.allow_support_recv else False
                vnum += 1

            if len(defense_states) and config['farms']['farm']:
                for vil in self.villages:
                    print("Syncing attack states")
                    vil.def_man.my_other_villages = defense_states

            sleep = 0
            active_h = [int(x) for x in config['bot']['active_hours'].split('-')]
            get_h = time.localtime().tm_hour
            if get_h in range(active_h[0], active_h[1]):
                sleep = config['bot']['active_delay']
            else:
                if config['bot']['inactive_still_active']:
                    sleep = config['bot']['inactive_delay']

            sleep += random.randint(20, 120)
            dtn = datetime.datetime.now()
            dt_next = dtn + datetime.timedelta(0, sleep)
            self.runs += 1
            if self.runs % 5 == 0:
                print("Optimizing farms")
                VillageManager.farm_manager()
            print("Dead for %f.2 minutes (next run at: %s)" % (sleep / 60, dt_next.time()))
            time.sleep(sleep)

    def start(self):
        if not os.path.exists("cache"):
            os.mkdir("cache")
        if not os.path.exists(os.path.join("cache", "attacks")):
            os.mkdir(os.path.join("cache", "attacks"))
        if not os.path.exists(os.path.join("cache", "reports")):
            os.mkdir(os.path.join("cache", "reports"))
        if not os.path.exists(os.path.join("cache", "villages")):
            os.mkdir(os.path.join("cache", "villages"))
        if not os.path.exists(os.path.join("cache", "world")):
            os.mkdir(os.path.join("cache", "world"))
        if not os.path.exists(os.path.join("cache", "logs")):
            os.mkdir(os.path.join("cache", "logs"))
        if not os.path.exists(os.path.join("cache", "managed")):
            os.mkdir(os.path.join("cache", "managed"))
        if not os.path.exists(os.path.join("cache", "hunter")):
            os.mkdir(os.path.join("cache", "hunter"))

        self.daemon = True
        if self.daemon:
            print("Running in daemon mode")
            self.run()
            while 1:
                self.should_run = True
                self.wrapper.endpoint = None
                self.run()
        else:
            self.run()


for x in range(3):
    t = TWB()
    try:
        t.start()
    except Exception as e:
        t.wrapper.reporter.report(0, "TWB_EXCEPTION", str(e))
        print("I crashed :(   %s" % str(e))
        traceback.print_exc()
        pass
