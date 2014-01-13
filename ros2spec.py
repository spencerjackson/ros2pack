#!/usr/bin/python3

import subprocess
import sys
import re
import os.path
import xml.etree.ElementTree as etree
import argparse
import urllib.request

import pdb
# Encapsulates a list of dependencies
class DependencyStore:

  _cache = {}

  class Dependency:
    def __init__(self, name):
      self._name = name
      self.resolve()

    def __str__(self):
      return self._resolved_name

    def resolve(self):
      if not subprocess.call(
        ['rospack','find',self._name], stdout = subprocess.DEVNULL, 
        stderr = subprocess.DEVNULL
      ):
        self._resolved_name = self._name
        return

      with subprocess.Popen(
        ['rosdep', '--os=opensuse:13.1', 'resolve', self._name], stdout = subprocess.PIPE, 
        stderr = subprocess.DEVNULL, universal_newlines=True
      ) as rosdep_stream:
        rosdep_result = rosdep_stream.stdout.readlines()
        if len(rosdep_result) == 2:
          self._resolved_name = rosdep_result[1]
        else:
          print(
"""The dependency {name} could not be found by either both rospack or rosdep.
Maybe you forgot to source the appropriate setup.bash, or there is no rosdep
binding for {name} for this OS?""".format(name=self._name))
          exit(1)

  def get_dependency(name):
    if name not in DependencyStore._cache:
      DependencyStore._cache[name] = DependencyStore.Dependency(name) 
    return DependencyStore._cache[name]

  def __init__(self, buildtool_depends, build_depends, run_depends):
    self._build = {p:DependencyStore.get_dependency(p) for p in build_depends + buildtool_depends + ['catkin', 'gtest']}
    self._run = {p:DependencyStore.get_dependency(p) for p in run_depends}

  def __str__(self):
    return "Build: {b}\nRun: {r}".format(b = self._build.__str__(), 
                                         r = self._run.__str__())
  
  def build_packages(self):
    return self._build.values()

  def run_packages(self):
    return self._run.values()

# Extracts the text from a node, stripping any tags and removing excess whitespace
def extract_all_text(element):
  mod_lst = []
  for text in element.itertext():
    if type(text) == list:
      text = "".join(text)
    mod_lst.append(text)
  return re.sub('\s+', ' ', "".join(mod_lst).strip())

class RPMSpec:

  def factory(packagePath, wsPath, override, distro):
    tree = etree.parse(packagePath+"/package.xml")
    root = tree.getroot()
    name = root.find('name').text
    version = root.find('version').text
    url = root.find('url').text

    if override.description != None:
      description = override.description
    else:
      description = extract_all_text(root.find('description'))
      description = description[0].upper() + description[1:]
    
    if override.summary != None:
      summary = override.summary
    else:
      summary = description.split(".", 1)[0]
    
    license = root.find('license').text
    with subprocess.Popen(
      ['wstool', 'info', '-t', wsPath, '--only', 'cur_uri,version', name], 
      stdout = subprocess.PIPE, universal_newlines = True
    ) as wstool:
      str_out = re.sub('\n', '', wstool.stdout.readline())
      if "ros-gbp" in str_out:
        source = re.sub('\.git,', '/archive/', str_out) + '.tar.gz'
        print("ros-gbp package detected. URL: " + source)
      else:
        source = re.sub(',.*', '', str_out)

    def elementText(element):
      return element.text

    dependencies = DependencyStore(
      list(map(elementText, root.findall('buildtool_depend'))),
      list(map(elementText, root.findall('build_depend'))),
      list(map(elementText, root.findall('run_depend')))
    )

    has_python = os.path.isfile(packagePath + "/setup.py")

    if root.find("export") == None:
      is_metapackage = False
    else:
      is_metapackage = root.find("export").find("metapackage") != None

    return RPMSpec(name, version, source, url, description, summary, 
                   license, dependencies, has_python, is_metapackage, distro)

  def __init__(self, name, version, source, url, description, summary, license, 
               dependencies, has_python, is_metapackage, distro):
    self.name = name
    self.version = version
    self.source = source
    self.url = url
    #self.patches = patches
    self.description = description
    self.summary = summary
    self.license = license
    self.dependencies = dependencies
    self.has_python = has_python
    self.is_metapackage = is_metapackage
    self.distro = distro

  def generate_service(self, stream):
    
    download_files_srv = """  <service name="download_files"/>"""
    tar_scm_srv = """  <service name="tar_scm">
    <param name="url">{source}</param>
    <param name="version">{version}</param>
    <param name="revision">master</param>
    <param name="scm">git</param> 
  </service>
""".format(source = self.source, version = self.version)

    stream.write("""<services>
{srv}
</services>
""".format(srv = (download_files_srv if "ros-gbp" in self.source else tar_scm_srv)))

  def render(self, stream):
    header_template = """%define __pkgconfig_path {{""}}

Name:           {pkg_name}
Version:        {version}
Release:        0
License:        {license}
Summary:        {summary}
Url:            {url}
Group:          Productivity/Scientific/Other
Source0:        {source}
Source1:        {pkg_name}-rpmlintrc
"""

    header_template += """BuildRequires:  python-devel
BuildRequires:  gcc-c++
BuildRequires:  python-rosmanifestparser
"""

    # correction for tar_scm
    if re.search("(\.git)$", self.source):
      src = self.name + '-' + self.version
    else:
      src = self.source

    stream.write(header_template.format(pkg_name = self.name,
                                        version = self.version, license = self.license,
                                        summary = self.summary, url = self.url,
                                        source = src))

    for build_dependency in sorted(map(str, self.dependencies.build_packages())):
      stream.write("BuildRequires:  {0}\n".format(build_dependency))
    for run_dependency in sorted(map(str, self.dependencies.run_packages())):
      stream.write("Requires:       {0}\n".format(run_dependency))
    stream.write("\n%description\n{0}\n".format(self.description))

    body = """
%define install_dir {install_space}
%define catkin_make %{{install_dir}}/bin/catkin_make_isolated

%prep
%setup -q -c -n workspace
mv * {name}
"""
    #patch_number = 0
    #for patch in self.patches:
    #  body += "%patch{0} -p0\n".format(patch_number)
    body += """mkdir src
mv {name} src
%build
source %{{install_dir}}/setup.bash
DESTDIR=%{{?buildroot}} %{{catkin_make}} -DCMAKE_INSTALL_PREFIX=%{{install_dir}} -DSETUPTOOLS_DEB_LAYOUT="OFF"

%install
source %{{install_dir}}/setup.bash
DESTDIR=%{{?buildroot}} %{{catkin_make}} --install -DCMAKE_INSTALL_PREFIX=%{{install_dir}}
if [ -f %{{buildroot}}/opt/ros/hydro/.catkin ];
then
  rm %{{?buildroot}}%{{install_dir}}/.catkin \
     %{{?buildroot}}%{{install_dir}}/.rosinstall \
     %{{?buildroot}}%{{install_dir}}/env.sh \
     %{{?buildroot}}%{{install_dir}}/_setup_util.py \
     %{{?buildroot}}%{{install_dir}}/setup*
fi
{pkgconfig}
rosmanifestparser {name} build/install_manifest.txt %{{?buildroot}} {has_python}

%files -f ros_install_manifest
%defattr(-,root,root)

%changelog
"""
    pkg_config_cmds = """mkdir -p %{{?buildroot}}%{{install_dir}}/share/pkgconfig
mv %{{?buildroot}}%{{install_dir}}/lib/pkgconfig/{name}.pc %{{?buildroot}}%{{install_dir}}/share/pkgconfig/
rmdir %{{?buildroot}}%{{install_dir}}/lib/pkgconfig
""".format(name=self.name)

    stream.write(body.format(
      pkgconfig = pkg_config_cmds if not self.is_metapackage else '',
      name = self.name, has_python = self.has_python, 
      install_space = "/opt/ros/" + self.distro))

# Allows overriding summary and description, and allows ignoring a package
class PackageOverride:
  def __init__(self, summary = None, description = None, patches = list(), ignore = False):
    self.summary = summary
    self.description = description
    self.patches = patches
    self.ignore = ignore

def generate_override(element):
  summary = element.find('summary')
  if summary != None:
    summary = extract_all_text(summary)
  description = element.find('description')
  if description != None:
    description = extract_all_text(description)
  ignore = (element.find('ignore') != None)
  return PackageOverride(summary, description, ignore)

if __name__ == '__main__':
  parser = argparse.ArgumentParser(description = "Generate RPM spec files from ROS packages")
  parser.add_argument('workspace', type = str,
                      help = 'path to the root of the workspace')
  parser.add_argument('destination', type = str,
                      help = 'path to the spec root')
  parser.add_argument('--packages', type = str, dest = 'packages', nargs = '+',
                      help = 'process only the specifed packages')
  parser.add_argument('--skip', type = str, dest = 'skipped', nargs = '+',
                      help = 'skip the specified packages')
  parser.add_argument('--local', dest = 'remote', action = 'store_false',
                      help = 'don\'t upload results to server')
  parser.add_argument('--remote', dest = 'remote', action = 'store_true',
                      help = 'upload results to server; set by default')
  parser.add_argument('--resume-at', type = str, dest = 'pack_resume', nargs = '?',
                      help = 'if the script failed previously, resume at the specified package')
  parser.add_argument('--distro', type = str, dest = 'distro', nargs = '?',
                      help = 'the ROS distribution to install (default is hydro)')
  parser.set_defaults(remote = True, distro = 'hydro', skipped = [])
  args = parser.parse_args()

  workspace_config = etree.parse(args.workspace + '/.ros2spec.xml').getroot()
  overrides = dict()
  for package in workspace_config:
    overrides[package.attrib['name']] = generate_override(package)

  srcdir = args.workspace + '/src/'
  if args.packages == None:
    packages = [name for name in os.listdir(srcdir) if os.path.isdir(srcdir + name)]
  else:
    packages = args.packages

  print("Listing packages on server ...")
  with subprocess.Popen(
    ["osc", "list", args.destination.split('/')[-1]], 
    stdout = subprocess.PIPE, universal_newlines = True) as server_results:
    remote_packages = [line.replace('\n', '') for line in server_results.stdout]
  skip = args.pack_resume != None

  for package in packages:
    if skip and (package != args.pack_resume):
      continue
    else:
      skip = False

    if package in args.skipped:
      continue

    try:
      override = overrides[package]
    except KeyError:
      override = PackageOverride()
    if override.ignore:
      continue
    spec = RPMSpec.factory(srcdir + '/' + package, srcdir, override, args.distro)
    target_dir = args.destination + '/' + package
    os.chdir(args.destination)
    if package not in remote_packages:
      print("Package " + package + " was not found on server.")
      if (os.path.exists(target_dir)):
        print("""The package was not found on the server, but the directory was found locally.
Please resolve this manually before continuing.""")
        exit(1)
      print("Creating package " + package + " ...")
      subprocess.call(['osc', 'mkpac', target_dir])
      os.chdir(target_dir)
    else:
      if not os.path.exists(target_dir):
        print("Checking out package ...")
        subprocess.call(['osc', 'co', package])
        os.chdir(target_dir)
      else:
        os.chdir(target_dir)
        print("Updating existing package ...")
        subprocess.call(['osc', 'up'])

    print('Generating files in ' + target_dir + ' ...')
    with open(target_dir + '/_service', mode = "w") as srv_file:
      spec.generate_service(srv_file)
    with open(target_dir + '/' + spec.name + ".spec", mode = "w") as rpmSpec:
      spec.render(rpmSpec)
    with open(target_dir + '/' + spec.name + "-rpmlintrc", mode = "w") as lintFile:
      lintFile.write("""setBadness('devel-file-in-non-devel-package', 0)
setBadness('shlib-policy-name-error', 0)""")

    subprocess.check_call(['osc', 'addremove'])
    with subprocess.Popen(["osc", "st"], stdout = subprocess.PIPE) as status:
      if status == '':
        print("No changes to commit.")
        continue
    if (args.remote):
      print("Performing check-in...")
      subprocess.check_call(['osc', 'ci', '-m', '"ros2spec automated check-in"'])
      
