#!/bin/bash -x

pvcreate /dev/sda /dev/sdb /dev/sdc /dev/sdd /dev/sde /dev/sdf

vgcreate -y vg1 /dev/sda /dev/sdb
vgcreate -y vg2 /dev/sdc /dev/sdd
vgcreate -y vg3 /dev/sde /dev/sdf

lvcreate -y -n lv1_vg1 -L 1G vg1
lvcreate -y -n lv2_vg1 -l 10 vg1

lvcreate -y -n lv1_vg2 -L 1G vg2
lvcreate -y -n lv2_vg2 -l 20 vg2

lvcreate -y -n lv1_vg3 -L 1G vg3
lvcreate -y -n lv2_vg3 -L 1G vg3
lvcreate -y -n lv3_vg3 -l 30 vg3
