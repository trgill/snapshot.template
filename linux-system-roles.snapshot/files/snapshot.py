#!/usr/bin/python
import argparse
import json
import logging
import math
import errno
import os
import subprocess
import sys
import logging

# from ansible.module_utils.basic import AnsibleModule
# # TODO should move these functions to another file - but need to figure out how Ansible
# from ansible.module_utils.snapshot.util import  run_command, percentage, percentof, SnapshotStatus, LvmBug


logger = logging.getLogger("snapshot-role")

MAX_LVM_NAME = 127


class LvmBug(RuntimeError):
	"""
	Things that are clearly a bug with lvm itself.
	"""
	def __init__(self, msg):
		super().__init__(msg)

	def __str__(self):
		return "lvm bug encountered: %s" % ' '.join(self.args)

class SnapshotStatus():
    SNAPSHOT_OK = 0
    ERROR_INSUFFICENT_SPACE = 1
    ERROR_ALREADY_DONE = 2
    ERROR_SNAPSHOT_FAILED = 3
    ERROR_REMOVE_FAILED = 4
    ERROR_REMOVE_FAILED_NOT_SNAPSHOT = 5
    ERROR_LVS_FAILED = 6
    ERROR_NAME_TOO_LONG = 7
    ERROR_ALREADY_EXISTS = 8
    ERROR_NAME_CONFLICT = 9
    ERROR_VG_NOTFOUND = 10
    ERROR_LV_NOTFOUND = 11
    
# what percentage is part of whole
def percentage(part, whole):
  return 100 * float(part)/float(whole)

# what is number is percent of whole
def percentof(percent, whole):
  return float(whole) / 100 * float(percent)

def set_up_logging(log_dir="/tmp", log_prefix="snapshot_role"):

    logger.setLevel(logging.DEBUG)

    def make_handler(path, prefix, level):
        log_file = "%s/%s.log" % (path, prefix)
        log_file = os.path.realpath(log_file)
        handler = logging.FileHandler(log_file)
        handler.setLevel(level)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s/%(threadName)s: %(message)s")
        handler.setFormatter(formatter)
        return handler

    handler = make_handler(log_dir, log_prefix, logging.DEBUG)
    stdout_handler = logging.StreamHandler(stream=sys.stdout)

    logger.addHandler(handler)
    logger.addHandler(stdout_handler)


def run_command(argv, stdin=None):

    logger.info("Running... %s", " ".join(argv))
 
    try:
        proc = subprocess.Popen(argv,
                                stdin=stdin,
                                stdout=subprocess.PIPE,
                                close_fds=True)

        out, err = proc.communicate()
    
        out = out.decode("utf-8")

    except OSError as e:
        print("Error running %s: %s", argv[0], e.strerror)
        raise
    
    logger.info("Return code: %d", proc.returncode)
    for line in out.splitlines():
        logger.info("%s", line)

    return (proc.returncode, out)

def check_positive(value):
    try:
        value = int(value)
        if value <= 0:
            raise argparse.ArgumentTypeError("{} is not a positive integer".format(value))
    except ValueError:
        raise Exception("{} is not an integer".format(value))
    return value

def round_up(value, multiple):
    return value + (multiple - (value % multiple))

def lvm_full_report_json():

    report_command = ["lvm", "fullreport",
                "--units", "B", "--nosuffix",
                "--configreport", "vg", "-o",  "vg_name,vg_uuid,vg_size,vg_free",
                "--configreport", "lv", "-o",  "lv_uuid,lv_name,lv_full_name,lv_path,lv_size,origin,origin_size,pool_lv,lv_tags,lv_attr,vg_name,data_percent,metadata_percent,pool_lv",
                "--configreport", "pv", "-o",  "pv_name",
                "--reportformat", "json"]

    rc, output = run_command(report_command)

    if rc:
        raise LvmBug("'fullreport' failed '%d'" % rc)
    try:
        lvm_json = json.loads(output)
    except ValueError as error:
        raise LvmBug("'fullreport' decode failed :", error)

    return lvm_json

def get_snapshot_name(lv_name, prefix, suffix):
    return prefix + lv_name + suffix

def lvm_lv_exists(vg_name, lv_name):
    lvs_command = ["lvs", "--reportformat", "json", vg_name + "/" + lv_name]

    rc, output = run_command(lvs_command)

    if rc == 0:
        return SnapshotStatus.SNAPSHOT_OK, True
    else:
        return SnapshotStatus.SNAPSHOT_OK, False

def lvm_is_owned(lv_name, prefix, suffix):

    if not lv_name.startswith(prefix) or not lv_name.endswith(suffix):
        return False
    
    return True

def lvm_is_snapshot(vg_name, snapshot_name):

    lvs_command = ["lvs", "--reportformat", "json", vg_name + "/" + snapshot_name]

    rc, output = run_command(lvs_command)

    if rc:
        return SnapshotStatus.ERROR_LVS_FAILED, None
    
    lvs_json = json.loads(output)

    lv_list = lvs_json["report"]

    if len(lv_list) > 1 or len(lv_list[0]["lv"]) > 1:
        raise LvmBug("'lvs' returned more than 1 lv :", rc)
        
    lv = lv_list[0]["lv"][0]

    lv_attr = lv["lv_attr"]

    if len(lv_attr) == 0:
            raise LvmBug("'lvs' zero length attr :", rc)

    if lv_attr[0] == 's':
        return SnapshotStatus.SNAPSHOT_OK, True
    else:
        return SnapshotStatus.SNAPSHOT_OK, False

def lvm_snapshot_remove(vg_name, snapshot_name):
    
    rc, is_snapshot = lvm_is_snapshot(vg_name, snapshot_name)

    if rc != SnapshotStatus.SNAPSHOT_OK:
        raise LvmBug("'lvs' failed '%d'" % rc)

    if not is_snapshot:
        return SnapshotStatus.ERROR_REMOVE_FAILED_NOT_SNAPSHOT, snapshot_name + " is not a snapshot"

    remove_command = ["lvremove", "-y", vg_name + "/" + snapshot_name]

    rc, output = run_command(remove_command)

    if rc:
        return SnapshotStatus.ERROR_REMOVE_FAILED, output
    
    return SnapshotStatus.SNAPSHOT_OK, ""


def snapshot_lv(vg_name, lv_name, prefix, suffix, snap_size):
    
    snapshot_name = get_snapshot_name(lv_name, prefix, suffix)

    rc, lv_exists = lvm_lv_exists(vg_name, snapshot_name)

    if lv_exists:
        if lvm_is_snapshot(vg_name, snapshot_name):
            return SnapshotStatus.ERROR_ALREADY_EXISTS, "Snapshot of :" + vg_name + "/" + lv_name + " already exists"
        else:
            return SnapshotStatus.ERROR_NAME_CONFLICT, "LV with name :" + snapshot_name + " already exits"

    snapshot_command = ["lvcreate", "-s", "-n", snapshot_name, "-L", str(snap_size) + "B", vg_name + "/" + lv_name]

    rc, output = run_command(snapshot_command)

    if rc:
        return SnapshotStatus.ERROR_SNAPSHOT_FAILED, output
    
    return SnapshotStatus.SNAPSHOT_OK, output


def check_space_for_snapshots(vg, lvs, lv_name, required_percent):
    vg_free = int(vg["vg_free"])
    total_lv_used = 0

    print("VG: ", vg["vg_name"], " free : ", vg["vg_free"])
    for lv in lvs:
        if lv_name and lv["lv_name"] != lv_name:
            continue
        print("\tLV: ",  lv["lv_name"], " size : ", lv["lv_size"])
        total_lv_used += int(lv["lv_size"])
    
    print("\tLV: total ",  total_lv_used)

    space_neeed = percentof(required_percent, total_lv_used)

    print("Space needed: ",  f'{space_neeed:.2f}')

    if vg_free >= space_neeed:
        return SnapshotStatus.SNAPSHOT_OK 
    
    return SnapshotStatus.ERROR_INSUFFICENT_SPACE

def check_name_for_snapshot(vg_name, lv_name, prefix, suffix):
    if len(vg_name) + len(lv_name) + len(prefix) + len(suffix) > MAX_LVM_NAME:
        return SnapshotStatus.ERROR_NAME_TOO_LONG
    else:
        return SnapshotStatus.SNAPSHOT_OK

def snapshot_lvs(required_space_available_percent, vg_name, lv_name, prefix, suffix, check_only):
    lvm_json = lvm_full_report_json()
    report = lvm_json["report"]
    vg_found = False
    lv_found = False


    for list_item in report:

        if vg_name and list_item["vg"][0]["vg_name"] != vg_name:
            continue
        vg_found = True
        for lvs in list_item["lv"]:
            if vg_name and lvs["lv_name"] != lv_name:
                continue
            lv_found = True
            if check_name_for_snapshot(list_item["vg"][0]["vg_name"], lvs["lv_name"], prefix, suffix):
                return SnapshotStatus.ERROR_NAME_TOO_LONG, "Resulting snapshot name would exceed LVM maximum"

        lvs = list_item["lv"]
        volume_group = list_item["vg"][0]

        if check_space_for_snapshots(volume_group, lvs, lv_name, required_space_available_percent):
            return SnapshotStatus.ERROR_INSUFFICENT_SPACE, "Insufficent space to for snapshots"

    if not check_only:
        # Take Snapshots
        for list_item in report:
            if vg_name and list_item["vg"][0]["vg_name"] != vg_name:
                continue

            volume_group = list_item["vg"][0]

            for lv in list_item["lv"]:
                if lv_name and lv["lv_name"] != lv_name:
                    continue
                
                # Make sure the source LV isn't a snapshot.
                rc, is_snapshot = lvm_is_snapshot(list_item["vg"][0]["vg_name"], lv["lv_name"])

                if rc != SnapshotStatus.SNAPSHOT_OK:
                    raise LvmBug("'lvs' failed '%d'" % rc)
                
                if is_snapshot:
                    continue

                lv_size = int(lv["lv_size"])
                snap_size = round_up(math.ceil(percentof(required_space_available_percent, lv_size)), 512)
                rc, message = snapshot_lv(list_item["vg"][0]["vg_name"], lv["lv_name"], prefix, suffix, snap_size)

                # TODO: Should the exiting snapshot be removed and be updated?
                if rc == SnapshotStatus.ERROR_ALREADY_EXISTS:
                    continue

                if rc != SnapshotStatus.SNAPSHOT_OK:
                    return SnapshotStatus.ERROR_SNAPSHOT_FAILED, message

    if vg_name and not vg_found:
        return SnapshotStatus.ERROR_VG_NOTFOUND, "Volume does not exist"
    if lv_name and not lv_found:
        return SnapshotStatus.ERROR_LV_NOTFOUND, "LV does not exist"

    return SnapshotStatus.SNAPSHOT_OK, ""

def snapshot_cleanup(volume_group, logical_volume, prefix, suffix):
    rc = SnapshotStatus.SNAPSHOT_OK
    message = ""
    lvm_json = lvm_full_report_json()
    report = lvm_json["report"]

    for list_item in report:
        vg_name = list_item["vg"][0]["vg_name"]

        if volume_group and volume_group != vg_name:
            continue

        for lvs in list_item["lv"]:
            
            lv_name = lvs["lv_name"]

            if logical_volume and logical_volume != lv_name:
                continue

            if not lvm_is_owned(lv_name, prefix, suffix):
                continue

            rc, message = lvm_snapshot_remove(vg_name, lvs["lv_name"])

            if rc != SnapshotStatus.SNAPSHOT_OK:
                break

        if volume_group:
            break

    return rc, message


def snapshot_cmd(args):
    print("snapshot_cmd: ", args.operation, args.required_space_available_percent, args.volume_group, args.logical_volume, args.prefix,args.suffix)
    rc = SnapshotStatus.SNAPSHOT_OK
    message = ""
    if args.all and args.volume_group:
        print("-all and --volume_group are mutually exclusive: ", args.operation)
        exit(1)

    if not args.all and args.volume_group is None:
        print("must specify either --all or a volume group: ", args.operation)
        exit(1)

    if args.all:
        rc, message = snapshot_lvs(args.required_space_available_percent, None, None, args.prefix,args.suffix, False)
    elif args.volume_group and args.logical_volume is None:
        rc, message = snapshot_lvs(args.required_space_available_percent, args.volume_group, None, args.prefix,args.suffix, False)
    else:
        rc, message = snapshot_lvs(args.required_space_available_percent, args.volume_group, args.logical_volume, args.prefix, args.suffix, False)
    return rc, message

def check_cmd(args):

    if args.all and args.volume_group:
        print("-all and --volume_group are mutually exclusive: ", args.operation)
        exit(1)

    if not args.all and args.volume_group is None:
        print("must specify either --all or a volume group: ", args.operation)
        exit(1)

    if args.all:
        rc, message = snapshot_lvs(args.required_space_available_percent, None, None, args.prefix,args.suffix, True)
    elif args.volume_group and args.logical_volume is None:
        rc, message = snapshot_lvs(args.required_space_available_percent, args.volume_group, None, args.prefix,args.suffix, True)
    else:
        rc, message = snapshot_lvs(args.required_space_available_percent, args.volume_group, args.logical_volume, args.prefix, args.suffix, True)
    return rc, message

def clean_cmd(args):
    print("clean_cmd: ", args.operation, args.volume_group, args.logical_volume, args.suffix, args.prefix)

    if args.all and args.volume_group:
        print("-all and --volume_group are mutually exclusive: ", args.operation)
        exit(1)

    return snapshot_cleanup(args.volume_group, args.logical_volume, args.prefix, args.suffix)

def print_result(rc, message):
    if rc != SnapshotStatus.SNAPSHOT_OK:
        print("rc = ", rc, " message : ", message)

if __name__ == "__main__":
    set_up_logging()

    parser = argparse.ArgumentParser(description='Snapshot Operations')
    
    # sub-parsers
    subparsers = parser.add_subparsers(dest='operation', help='Available operations')

    # sub-parser for 'snapshot' 
    snapshot_parser = subparsers.add_parser('snapshot', help='Snapshot given VG/LVs')
    snapshot_parser.set_defaults(func=snapshot_cmd)
    snapshot_parser.add_argument('-a', '--all', action='store_true', default=False, dest='all',help='snapshot all VGs and LVs')
    snapshot_parser.add_argument('-vg', '--volumegroup', action='store', default = None, dest='volume_group', help='volume group to snapshot')
    snapshot_parser.add_argument('-lv', '--logicalvolume', action='store', default = None, dest='logical_volume', help='logical volume to snapshot')
    snapshot_parser.add_argument('-r', '--percentavailable', dest='required_space_available_percent', required=False, type=int, choices=range(10,100), 
                                default = 20, help='percent of required space in the volume group to be reserved for snapshot')
    snapshot_parser.add_argument('-s', '--suffix', dest='suffix', type=str,  help='suffix to add to volume name for snapshot')
    snapshot_parser.add_argument('-p', '--prefix', dest='prefix', type=str,  help='prefix to add to volume name for snapshot')

    # sub-parser for 'check'
    check_parser = subparsers.add_parser('check', help='Check space for given VG/LV')
    check_parser.add_argument('-a', '--all', action='store_true', default=False, dest='all',help='snapshot all VGs and LVs')
    check_parser.add_argument('-vg', '--volumegroup', action='store', default = None, dest='volume_group', help='volume group to check')
    check_parser.add_argument('-lv', '--logicalvolume', action='store', default = None, dest='logical_volume', help='logical volume to snapshot')
    check_parser.add_argument('-r', '--percentavailable', dest='required_space_available_percent',  required=False, type=int, choices=range(10,100), 
                                help='percent of required space in the volume group to be reserved for snapshot')
    check_parser.add_argument('-s', '--suffix', dest='suffix', type=str,  help='suffix to add to volume name for snapshot')
    check_parser.add_argument('-p', '--prefix', dest='prefix', type=str,  help='prefix to add to volume name for snapshot')
    check_parser.set_defaults(func=check_cmd)
    

    # sub-parser for 'clean'
    clean_parser = subparsers.add_parser('clean', help='Cleanup snapshots')
    clean_parser.add_argument('-a', '--all', action='store_true', default=False, dest='all',help='snapshot all VGs and LVs')
    clean_parser.add_argument('-vg', '--volumegroup', action='store', default = None, dest='volume_group', help='volume group to check')
    clean_parser.add_argument('-lv', '--logicalvolume', action='store', default = None, dest='logical_volume', help='logical volume to remove')
    clean_parser.add_argument('-s', '--suffix', dest='suffix', type=str,  help='suffix to add to volume name for removal')
    clean_parser.add_argument('-p', '--prefix', dest='prefix', type=str,  help='prefix to add to volume name for removal')
    clean_parser.set_defaults(func=clean_cmd)
  

    args = parser.parse_args()
    rc, message = args.func(args)
    print_result(rc, message)


