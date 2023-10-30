#!/bin/bash -x

export ANSIBLE_LOG_PATH=/tmp/ansible.log
ansible-playbook -vvv -K --flush-cache -i inventory.yml tests/snapshot-system.yml
