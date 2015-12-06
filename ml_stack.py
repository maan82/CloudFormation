# /usr/bin/env python

"""MarkLogic CloudFormation template generator.

Usage:
    ml_stack.py [-v] [-f CONFIGURATION_FILE]

Options:
    -f CONFIGURATION_FILE       MarkLogic cluster Configuration file [default: conf/ml_master.json]
"""
from docopt import docopt
from troposphere import Ref, Template
import troposphere.ec2 as ec2
import troposphere.elasticloadbalancing as elb
import troposphere.autoscaling as autoscaling
import json
import logging
import os
from troposphere import Base64, Join



def create_name(base, az, instanceNumber):
    return base + az + str(instanceNumber)


def get_subnet_id(aws_config, az):
    return get_private_subnets(aws_config)["Sandbox_DEV_PVT_1" + az]


def get_private_subnets(aws_config):
    return aws_config["Subnets"]["Sandbox_DEV"]["PrivateSubnets"]


def create_tags(config, base, az, instanceNumber):
    tags = []
    for tag in config["Tags"]:
        tags.append([tag + "-" + base + "-zone-" + az + "-instance-" + str(instanceNumber)])
    return tags

def create_launch_config(aws_config, config, az, instanceNumber, security_groups):
    launch_configuration = autoscaling.LaunchConfiguration(create_name("LaunchConfig", az, instanceNumber))
    """
            'AssociatePublicIpAddress': (boolean, False),
        'BlockDeviceMappings': (list, False),
        'ClassicLinkVPCId': (basestring, False),
        'ClassicLinkVPCSecurityGroups': ([basestring], False),
        'EbsOptimized': (boolean, False),
        'IamInstanceProfile': (basestring, False),
        'ImageId': (basestring, True),
        'InstanceId': (basestring, False),
        'InstanceMonitoring': (boolean, False),
        'InstanceType': (basestring, True),
        'KernelId': (basestring, False),
        'KeyName': (basestring, False),
        'Metadata': (Metadata, False),
        'PlacementTenancy': (basestring, False),
        'RamDiskId': (basestring, False),
        'SecurityGroups': (list, False),
        'SpotPrice': (basestring, False),
        'UserData': (basestring, False),
    """
    launch_configuration.EbsOptimized = config["EbsOptimized"]
    launch_configuration.IamInstanceProfile = config["IamInstanceProfile"]
    launch_configuration.ImageId = config["MarkLogicAMIImageId"]
    launch_configuration.InstanceType = config["InstanceType"]
    launch_configuration.KeyName = config["KeyName"]
    security_group_list = []
    for group in security_groups:
        security_group_list.append(Ref(group))

    launch_configuration.SecurityGroups = security_group_list
    launch_configuration.UserData = Base64(Join('', [
        "#!/bin/bash\n",
        "echo \"<?php phpinfo(); ?>\" > /tmp/userdata.log"
    ]))
    return launch_configuration



def create_instance(aws_config, config, az, instanceNumber):
    instance = ec2.Instance(create_name("MarkLogic", az, instanceNumber))
    instance.AvailabilityZone = az
    instance.EbsOptimized = config["EbsOptimized"]
    instance.ImageId = config["MarkLogicAMIImageId"]
    instance.InstanceType = config["InstanceType"]
    instance.SubnetId = get_subnet_id(aws_config, az)
    instance.Tags = create_tags(config, "instance", az, instanceNumber)
    instance.Tenancy = config["Tenancy"]
    return instance


def create_network_interface(aws_config, config, az, instanceNumber, group_set):
    network_interface = ec2.NetworkInterface(create_name("MarkLogicNetworkInterface", az, instanceNumber))
    network_interface.Description = "For MarkLogic zone " + az + " instance " + str(instanceNumber)
    group_set_list = []
    for group in group_set:
        group_set_list.append(Ref(group))
    network_interface.GroupSet = group_set_list
    network_interface.SubnetId = get_subnet_id(aws_config, az)
    network_interface.Tags = create_tags(config, "NetworkInterface", az, instanceNumber)
    return network_interface


def create_data_volume(aws_config, config, az, instanceNumber):
    data_volume = ec2.Volume(create_name("MarkLogicDataVolume", az, instanceNumber))
    data_volume.AvailabilityZone = az
    data_volumes = config["DataVolumes"]
    data_volume.Encrypted = data_volumes["Encrypted"]
    data_volume.Iops = data_volumes["Iops"]
    data_volume.Size = data_volumes["Size"]
    data_volume.Tags = create_tags(config, "DataVolume", az, instanceNumber)
    data_volume.VolumeType = data_volumes["VolumeType"]
    return data_volume


def create_config_volume(aws_config, config, az, instanceNumber):
    data_volume = ec2.Volume(create_name("MarkLogicConfigVolume", az, instanceNumber))
    data_volume.AvailabilityZone = az
    config_volumes = config["ConfigVolumes"]
    data_volume.Encrypted = config_volumes["Encrypted"]
    data_volume.Iops = config_volumes["Iops"]
    data_volume.Size = config_volumes["Size"]
    data_volume.Tags = create_tags(config, "ConfigVolume", az, instanceNumber)
    data_volume.VolumeType = config_volumes["VolumeType"]
    return data_volume


def create_load_balancer_security_group(aws_config, config):
    return ec2.SecurityGroup(
        "LoadBalancerSecurityGroup",
        GroupDescription="Enable HTTP/XDBC access on the inbound port",
        SecurityGroupIngress=[
            ec2.SecurityGroupRule(
                IpProtocol="tcp",
                FromPort=config["LoadBalancedPorts"]["FromPort"],
                ToPort=config["LoadBalancedPorts"]["ToPort"],
                CidrIp="0.0.0.0/0",
            )
        ],
        VpcId=aws_config["VpcId"]
    )


def create_cluster_security_group(aws_config, config):
    return ec2.SecurityGroup(
        "ClusterSecurityGroup",
        GroupDescription="Enable communication of cluster ports e.g. For nodes within cluster and replication between clusters.",
        SecurityGroupIngress=[
            ec2.SecurityGroupRule(
                IpProtocol="tcp",
                FromPort=config["ClusterPorts"]["FromPort"],
                ToPort=config["ClusterPorts"]["ToPort"],
                CidrIp="0.0.0.0/0",
            )
        ],
        VpcId=aws_config["VpcId"]
    )


def create_load_balancer(aws_config, config, security_groups):
    load_balancer = elb.LoadBalancer("MarkLogicLoadBalancer")
    config_load_balancer = config["LoadBalancer"]
    load_balancer.AppCookieStickinessPolicy = config_load_balancer["AppCookieStickinessPolicy"]
    draining_policy = config_load_balancer["ConnectionDrainingPolicy"]
    load_balancer.ConnectionDrainingPolicy = elb.ConnectionDrainingPolicy(
        Enabled=bool(draining_policy["Enabled"]),
        Timeout=draining_policy["Timeout"]
    )
    load_balancer.CrossZone = config_load_balancer["CrossZone"]
    health_check = config_load_balancer["HealthCheck"]
    load_balancer.HealthCheck = elb.HealthCheck(
        Target=health_check["Target"],
        HealthyThreshold=health_check["HealthyThreshold"],
        UnhealthyThreshold=health_check["UnhealthyThreshold"],
        Interval=health_check["Interval"],
        Timeout=health_check["Timeout"]
    )
    load_balancer.Listeners = config_load_balancer["Listeners"]
    load_balancer.Subnets = get_private_subnets(aws_config).values()
    security_groups_list = []
    for group in security_groups:
        security_groups_list.append(Ref(group))

    load_balancer.SecurityGroups = security_groups_list
    load_balancer.Tags = create_tags(config, "LoadBalancer", "allzones", None)
    return load_balancer


if __name__ == '__main__':
    arguments = docopt(__doc__)

    if arguments['-v']:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    # env = arguments["ENV"].lower()

    with open('conf/aws_config.json') as aws_config_file:
        aws_config = json.load(aws_config_file)

    with open(arguments["-f"]) as config_file:
        config = json.load(config_file)

    vpc = {
        "VpcId": aws_config["VpcId"]
    }

    zones = config.get("Zones", aws_config["Zones"])

    template = Template()
    template.add_version("2010-09-09")
    cluster_security_group = create_cluster_security_group(aws_config, config)
    load_balancer_security_group = create_load_balancer_security_group(aws_config, config)
    template.add_resource(cluster_security_group)
    template.add_resource(load_balancer_security_group)
    template.add_resource(create_load_balancer(aws_config, config, [load_balancer_security_group]))

    for az in zones:
        for instanceNumber in range(1, config["NumberOfInstancesPerZone"] + 1):
            launch_config = create_launch_config(aws_config, config, az, instanceNumber, [cluster_security_group])
            template.add_resource(launch_config)
            instance = create_instance(aws_config, config, az, instanceNumber)
            config_volume = create_config_volume(aws_config, config, az, instanceNumber)
            template.add_resource(ec2.VolumeAttachment(
                create_name("ConfigVolumeAttachment", az, instanceNumber),
                Device="/dev/xvdh",
                InstanceId=Ref(instance),
                VolumeId=Ref(config_volume)
            ))
            template.add_resource(config_volume)
            data_volume = create_data_volume(aws_config, config, az, instanceNumber)
            template.add_resource(ec2.VolumeAttachment(
                create_name("DataVolumeAttachment", az, instanceNumber),
                Device="/dev/xvdg",
                InstanceId=Ref(instance),
                VolumeId=Ref(data_volume)
            ))
            template.add_resource(data_volume)
            network_interface = create_network_interface(aws_config, config, az, instanceNumber, [cluster_security_group])
            template.add_resource(ec2.NetworkInterfaceAttachment(
                create_name("NetworkInterfaceAttachment", az, instanceNumber),
                DeleteOnTermination="false",
                DeviceIndex=1,
                InstanceId=Ref(instance),
                NetworkInterfaceId=Ref(network_interface)
            ))
            template.add_resource(network_interface)
            template.add_resource(instance)

    print(template.to_json())
