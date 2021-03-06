#!/usr/bin/env python3

import argparse, os, sys, hashlib, re, time, json, shutil, io
import typing

DATABASE_FIELD_NAME_INDEX = 'index'
DATABASE_FIELD_NAME_HASH = 'hash'
DATABASE_STORAGE_NAME = 'database.json'

class script_commands(object):
    seperate_database = 'seperate-database'
    import_project    = 'import-project'
    import_assets     = 'import-assets'
    rebuild_order     = 'rebuild-order'

    @classmethod
    def get_option_choices(cls):
        choice_list = []
        for name, value in vars(cls).items():
            if name.replace('_', '-') == value: choice_list.append(value)
        return choice_list

class ArgumentOptions(object):
    def __init__(self, data):
        if not data: return
        self.import_path = data.import_path # type: str
        self.work_path = data.work_path # type: str
        self.hash_size = data.hash_size # type: str
        self.file_types = data.file_type # type: list
        self.project_name = data.project_name # type: str
        self.project_path = data.project_path # type: str
        self.command = data.command # type: str
        self.with_copy = data.with_copy # type: bool
        self.with_date = data.with_date # type: bool
        self.years = data.year # type:list[str]
        self.repair = data.repair # type: bool

    def clone(self):
        result = ArgumentOptions(data=None)
        for name, value in vars(self).items():
            if name.startswith('__') or name.endswith('__'): continue
            result.__setattr__(name, value)
        return result

def repair_asset_times(asset_path:str):
    assert os.path.exists(asset_path)
    process = os.popen('exiftool -stay_open True -r -e -n -createdate {}'.format(asset_path))
    buffer = io.StringIO(process.read())
    process.close()
    while True:
        line = buffer.readline()  # type: str
        date_pattern = re.compile(r'\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$')
        if not line: break
        if line.startswith('===='):
            file_path = line[9:-1]
            if not asset_pattern.search(file_path): continue
            position = buffer.tell()
            exif_info = buffer.readline()  # type: str
            if not date_pattern.search(exif_info):
                buffer.seek(position, os.SEEK_SET)
                continue
            create_date = time.strptime(exif_info[-20:-1], '%Y:%m:%d %H:%M:%S')
            create_time = int(time.mktime(create_date))
            os.utime(file_path, (create_time, create_time))
            print('{} => {}'.format(file_path, time.strftime('%Y-%m-%dT%H:%M:%S', create_date)))

def import_assets_from_external(options:ArgumentOptions):
    asset_list = []
    for walk_path, _, file_name_list in os.walk(options.import_path):
        for file_name in file_name_list:
            target_location = os.path.join(walk_path, file_name)
            if file_name.startswith('.'): continue
            if not asset_pattern.search(file_name) or os.path.islink(target_location): continue
            asset_list.append(target_location)
    if options.repair:
        repair_asset_times(asset_path=options.import_path)
    import_assets(options, asset_list)

def import_assets(options:ArgumentOptions, asset_list:typing.List[str]):
    project_path = os.path.join(options.work_path, options.project_name)
    if not os.path.exists(project_path):
        os.makedirs(project_path)

    database = {} # type: dict[str,dict]
    def get_database(name:str, reload_from_disk = False)->dict:
        if name in database and not reload_from_disk:
            return database[name]
        database_path = os.path.join(project_path, name, DATABASE_STORAGE_NAME)
        data = {}
        if os.path.exists(database_path):
            try:
                with open(database_path, 'r+') as fp:
                    data = json.load(fp)
                    fp.close()
            except: pass
        for field_name in [DATABASE_FIELD_NAME_HASH, DATABASE_FIELD_NAME_INDEX]:
            if field_name not in data: data[field_name] = {}
        database[name] = data
        return data

    hash_size = int(options.hash_size)
    # generate incremental list
    increment_list, accept_map = [], {}
    for target_location in asset_list:
        timestamp = os.stat(target_location).st_birthtime
        mtime = time.localtime(os.path.getmtime(target_location))
        unick_map = get_database(name=str(mtime.tm_year)).get(DATABASE_FIELD_NAME_HASH)
        md5 = hashlib.md5()
        with open(target_location, 'r+b') as fp:
            md5.update(fp.read(hash_size))
            digest = md5.hexdigest()
            fp.close()
            if digest in unick_map or digest in accept_map:
                print('[DUP] {} {}'.format(digest, target_location))
                continue
            accept_map[digest] = target_location
            item = (timestamp, mtime, digest, target_location)
            increment_list.append(item)

    def camera_roll_sort(a, b):
        if a[0] != b[0]: return 1 if a[0] > b[0] else -1
        return 1 if a[-1] > b[-1] else -1

    from functools import cmp_to_key
    increment_list.sort(key=cmp_to_key(camera_roll_sort))
    # generate image move path
    live_map = {}
    for n in range(len(increment_list)):
        _, mtime, digest, src_location = increment_list[n]
        label = '%02d%02d' % (mtime.tm_year, mtime.tm_mon)
        index_map = get_database(name=str(mtime.tm_year)).get(DATABASE_FIELD_NAME_INDEX)
        unick_map = get_database(name=str(mtime.tm_year)).get(DATABASE_FIELD_NAME_HASH)
        # print(index_map)
        if label not in index_map: index_map[label] = 1
        common_path = src_location[:src_location.rfind('.')]
        sequence, reference_count = live_map.get(common_path) # keep live video and foto have the same sequence
        if reference_count >= 2: sequence = 0
        if not sequence:
            sequence = index_map.get(label)
            live_map[common_path] = sequence, 1
            index_map[label] += 1
        else:
            live_map[common_path] = sequence, reference_count + 1
        extension = src_location[src_location.rfind('.')+1:]
        file_name = '%s_%04d.%s' % (label, sequence, extension)
        dst_group_location = '%s/%04d' % (project_path, mtime.tm_year)
        if options.with_date:
            dst_group_location = os.path.join(dst_group_location, time.strftime('%Y-%m-%d', mtime))
        if not os.path.exists(dst_group_location):
            os.makedirs(dst_group_location)
        dst_location = '%s/%s' % (dst_group_location, file_name)
        assert not os.path.exists(dst_location), '{} => {}'.format(src_location, dst_location)
        unick_map[digest] = file_name
        if options.with_copy:
            shutil.copy(src_location, dst_location)
        else:
            shutil.move(src_location, dst_location)
        print(digest, '%s => %s' % (src_location, dst_location))

    for name, mini_database in database.items():
        write_database(mini_database, project_path=os.path.join(project_path, name))

def seperate_database(options:ArgumentOptions):
    database = json.load(open('{}/{}'.format(options.project_path, DATABASE_STORAGE_NAME), 'r+'))
    index_map = database.get(DATABASE_FIELD_NAME_INDEX) # type: dict
    assert index_map
    group_index_map = {} # type: dict[str:tuple[str, str]]
    for name, value in index_map.items():
        year = name[:4]
        if year not in group_index_map: group_index_map[year] = []
        group_index_map[year].append((name, value))
    hash_map = database.get(DATABASE_FIELD_NAME_HASH) # type: dict
    assert hash_map
    group_hash_map = {} # type: dict[str:tuple[str, str]]
    for hash, name in hash_map.items():
        year = name[:4]
        if year not in group_hash_map: group_hash_map[year] = []
        group_hash_map[year].append((hash, name))
    assert group_hash_map.keys() == group_index_map.keys()
    for year in group_index_map.keys():
        mini_project_path = os.path.join(options.project_path, year)
        assert os.path.exists(mini_project_path)
        mini_database = {}
        index_map = mini_database[DATABASE_FIELD_NAME_INDEX] = {}
        for key, value in group_index_map.get(year): index_map[key] = value
        hash_map = mini_database[DATABASE_FIELD_NAME_HASH] = {}
        for key, value in group_hash_map.get(year): hash_map[key] = value
        write_database(mini_database, project_path=mini_project_path)

def write_database(data:dict, project_path:str):
    database_path = os.path.join(project_path, DATABASE_STORAGE_NAME)
    with open(database_path, 'w+') as fp:
        json.dump(data, fp, indent=4)
        fp.close()
        print('database => {}'.format(database_path))

def import_assets_from_project(options:ArgumentOptions):
    asset_list = []
    for year in os.listdir(options.project_path):
        if not re.match(r'^\d{4}$', year): continue
        common_suffix_path = '{}/{}'.format(year, DATABASE_STORAGE_NAME)
        src_database_path = '{}/{}'.format(options.project_path, common_suffix_path)
        with open(src_database_path, 'r+') as fp:
            src_database = json.load(fp) # type: dict[str,dict[str, str]]
            fp.close()
        src_hash_map = src_database.get(DATABASE_FIELD_NAME_HASH)
        dst_database_path = '{}/{}/{}'.format(options.work_path, options.project_name, common_suffix_path)
        if os.path.exists(dst_database_path):
            with open(dst_database_path, 'r+') as fp:
                dst_database = json.load(fp) # type: dict[str,dict[str, str]]
                fp.close()
            dst_hash_map = dst_database.get(DATABASE_FIELD_NAME_HASH)
            if not dst_hash_map: dst_hash_map = {}
            for hash, name in src_hash_map.items():
                if hash not in dst_hash_map:
                    asset_list.append(os.path.join(options.project_path, year, name))
                else:
                    print(os.path.join(options.project_path, year, name))
        else:
            for hash, name in src_hash_map.items():
                asset_list.append(os.path.join(options.project_path, year, name))
    options.with_copy = True
    # print(vars(options))
    # print('\n'.join(asset_list))
    import_assets(options, asset_list)

def rebuild_order(options:ArgumentOptions):
    for year in options.years:
        mini_project_path = os.path.join(options.work_path, options.project_name, year)
        if not os.path.exists(mini_project_path): continue
        temp_project_path = '{}_temp'.format(mini_project_path)
        if os.path.exists(temp_project_path):
            shutil.rmtree(temp_project_path)
        os.rename(mini_project_path, temp_project_path)
        suboptions = options.clone()
        suboptions.command = script_commands.import_assets
        suboptions.import_path = temp_project_path
        import_assets_from_external(suboptions)
        shutil.rmtree(temp_project_path)

def main():
    arguments = argparse.ArgumentParser()
    arguments.add_argument('--command', '-c', default=script_commands.import_assets, choices=script_commands.get_option_choices())
    arguments.add_argument('--import-path', '-i', help='local folder path for walking through to import')
    arguments.add_argument('--work-path', '-w', default=os.path.expanduser('/Volumes/Remember/CameraRoll'), help='local folder path for moveing to')
    arguments.add_argument('--hash-size', '-s', type=int, default=1024*10, help='num of bytes for md5sum caculation')
    arguments.add_argument('--file-type', '-t', nargs='+', help='file extension types for keep-filter')
    arguments.add_argument('--project-name', '-n', help='album project name')
    arguments.add_argument('--project-path', '-p', help='album project path')
    arguments.add_argument('--year', '-y', nargs='+', help='year number from album project')
    arguments.add_argument('--repair', '-r', action='store_true', help='restore modified time from exif create date')
    arguments.add_argument('--with-copy', action='store_true', help='will copy asset file to destination instead of move when set')
    arguments.add_argument('--with-date', action='store_true', help='will put assets into date[%%Y-%%m-%%d] folders when set')
    options = ArgumentOptions(data=arguments.parse_args(sys.argv[1:]))

    global asset_pattern
    asset_pattern = re.compile(r'\.(JPG|MOV|MP4|CR2|HEIC)$', re.IGNORECASE)
    if options.file_types:
        asset_pattern = re.compile(r'\.(%s)$' % ('|'.join(options.file_types)), re.IGNORECASE)

    if options.command == script_commands.import_assets:
        assert options.import_path and os.path.exists(options.import_path)
        assert options.work_path and os.path.exists(options.work_path)
        assert options.hash_size >= 1024
        assert options.project_name
        import_assets_from_external(options)
    elif options.command == script_commands.seperate_database:
        assert options.project_path and os.path.exists(options.project_path)
        seperate_database(options)
    elif options.command == script_commands.import_project:
        assert options.project_path and os.path.exists(options.project_path)
        assert options.project_name
        import_assets_from_project(options)
    elif options.command == script_commands.rebuild_order:
        assert options.project_name
        assert options.years
        rebuild_order(options)

if __name__ == '__main__':
    main()
