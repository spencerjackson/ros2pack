import subprocess
import sys
import re
import os.path
import xml.etree.ElementTree as etree
import argparse
import urllib.request

# Remove me!
import pdb

PACKAGE_PREFIX = "ros-{0}"

# Encapsulates a list of dependencies
class DependencyStore:
  class Dependency:
    def __init__(self, name):
      self._name = name
      self._providedLocal = False

    @property
    def name(self):
      return self.name

    @property
    def providedLocal(self):
      return self._providedLocal

    @providedLocal.setter
    def providedLocal(self, value):
      self._providedLocal = value

    def __str__(self):
      if self._providedLocal:
        return PACKAGE_PREFIX.format(self._name)
      with subprocess.Popen(
        ['rosdep', 'resolve', self._name], 
        stdout=subprocess.PIPE, universal_newlines=True
      ) as rosdep_stream:
        rosdep_result = rosdep_stream.stdout.readlines()
        if len(rosdep_result) == 2:
          return rosdep_result[1]
      return self._name

  def __init__(self, buildtool_depends, build_depends, run_depends):
    self._build = {p:DependencyStore.Dependency(p) for p in build_depends + buildtool_depends}
    self._run = {p:DependencyStore.Dependency(p) for p in run_depends}

  def __str__(self):
    return (self._build + self._run).__str__()

  def mark(self, package_name):
    if package_name in self._build:
      self._build[package_name].providedLocal = True
    if package_name in self._run:
      self._run[package_name].providedLocal = True

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

def RPMSpec_factory(packagePath, wsPath, override):
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
  dependencies = DependencyStore(list(map(elementText,
                                          root.findall('buildtool_depend'))),
                                 list(map(elementText,
                                          root.findall('build_depend'))),
                                 list(map(elementText,
                                          root.findall('run_depend'))))
  with subprocess.Popen(
    ["wstool", "info", "-t", wsPath, "--only", "localname"], 
    stdout = subprocess.PIPE, universal_newlines = True
  ) as provided_results:
    for provided_result in provided_results.stdout:
      provided = provided_result.strip()
      dependencies.mark(provided)
  has_python = os.path.isfile(packagePath + "/setup.py")
  if root.find("export") == None:
    is_metapackage = False
  else:
    is_metapackage = root.find("export").find("metapackage") != None

  return RPMSpec(name, version, source, url, description, summary, 
                 license, dependencies, has_python, is_metapackage)

# Class to model an RPM spec
class RPMSpec:
  def __init__(self, name, version, source, url, description, summary, license, 
               dependencies, has_python, is_metapackage):
    self.name = name
    self.version = version
    self.source = source
    self.url = url
    self.description = description
    self.summary = summary
    self.license = license
    self.dependencies = dependencies
    self.has_python = has_python
    self.is_metapackage = is_metapackage

  def generate_service(self, stream):
    
    download_files_srv = """  <service name="download_files"/>"""
    tar_scm_srv = """  <service name="tar_scm">
    <param name="url">{source}</param>
    <param name="revision">master</param>
    <param name="scm">git</param> 
  </service>
""".format(source = self.source)

    stream.write("""<services>
{srv}
</services>
""".format(srv = (download_files_srv if "ros-gbp" in self.source else tar_scm_srv)))

  def render(self, stream):
    header_template = """%define __pkgconfig_path {{""}}

Name:		        {pkg_name}
Version:	      {version}
Release:	      0
License:	      {license}
Summary:	      {summary}
Url:	          {url}
Group:	        Productivity/Scientific/Other
Source0:	      {source}
Source1:	      {pkg_name}-rpmlintrc

BuildRequires:  python-devel
BuildRequires:  gcc-c++
BuildRequires:  python-rosmanifestparser
"""
    stream.write(header_template.format(pkg_name = PACKAGE_PREFIX.format(self.name),
                                        version = self.version, license = self.license,
                                        summary = self.summary, url = self.url,
                                        source = self.source))

    for build_dependency in self.dependencies.build_packages():
      stream.write("BuildRequires:	{0}\n".format(build_dependency))
    for run_dependency in self.dependencies.run_packages():
      stream.write("Requires:	      {0}\n".format(run_dependency))
    stream.write("\n%description\n{0}\n".format(self.description))

    body = """
%prep
%setup -q -c -n workspace
mv * {name}
mkdir src
mv {name} src

%build
CMAKE_PREFIX_PATH=/usr DESTDIR=%{{?buildroot}} catkin_make -DCMAKE_INSTALL_PREFIX=/usr -DSETUPTOOLS_DEB_LAYOUT="OFF"

%install
CMAKE_PREFIX_PATH=/usr DESTDIR=%{{?buildroot}} catkin_make install -DCMAKE_INSTALL_PREFIX=/usr
#rm %{{?buildroot}}/usr/.catkin %{{?buildroot}}/usr/.rosinstall \
#   %{{?buildroot}}/usr/env.sh %{{?buildroot}}/usr/_setup_util.py \
#   %{{?buildroot}}/usr/setup*
{pkgconfig}
rosmanifestparser {name} build/install_manifest.txt %{{?buildroot}} {has_python}

%files -f ros_install_manifest
%defattr(-,root,root)

%changelog
"""
    pkg_config_cmds = """mkdir -p %{{?buildroot}}/usr/share/pkgconfig
mv %{{?buildroot}}/usr/lib/pkgconfig/{name}.pc %{{?buildroot}}/usr/share/pkgconfig/
rmdir %{{?buildroot}}/usr/lib/pkgconfig
""".format(name = self.name)

    stream.write(body.format(name=self.name, has_python=self.has_python, 
                pkgconfig = pkg_config_cmds if not self.is_metapackage else ''))

# Allows overriding summary and description, and allows ignoring a package
class PackageOverride:
  def __init__(self, summary = None, description = None, ignore = False):
    self.summary = summary
    self.description = description
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
  parser.add_argument('--packages', type = str, dest = 'packages', nargs = '+',
                       help = 'process only the specifed packages')
  parser.add_argument('destination', type = str,
                      help = 'path to the spec root')
  parser.add_argument('--resume-at', type = str, dest = 'pack_resume', nargs = '?',
                      help = 'if the script failed previously, resume at the specified package')
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

  # subprocess.call(['osc', 'up'], cwd = args.destination)

  print("Listing packages on server ...")
  with subprocess.Popen(
    ["osc", "list", args.destination.split('/')[-1]], 
    stdout = subprocess.PIPE, universal_newlines = True) as server_results:
    remote_packages = [line.replace('\n', '') for line in server_results.stdout]

  skip = args.pack_resume != None
  for package in packages:
    if skip and package != args.pack_resume:
      continue
    else:
      skip = False

    try:
      override = overrides[package]
    except KeyError:
      override = PackageOverride()
    if override.ignore:
      continue
    spec = RPMSpec_factory(srcdir + '/' + package, srcdir, override)
    pack_formatted = PACKAGE_PREFIX.format(package)
    target_dir = args.destination + '/' + pack_formatted
    os.chdir(args.destination)

    if pack_formatted not in remote_packages:
      print("Package " + pack_formatted + " was not found on server.")
      if (os.path.exists(target_dir)):
        print("""The package was not found on the server, but the directory was found locally.
Please resolve this manually before continuing.""")
        exit(1)
      print("Creating package " + pack_formatted + " ...")
      subprocess.call(['osc', 'mkpac', target_dir])
      os.chdir(target_dir)
    else:
      if not os.path.exists(target_dir):
        print("Checking out package ...")
        subprocess.call(['osc', 'co', pack_formatted])
        os.chdir(target_dir)
      else:
        os.chdir(target_dir)
        print("Updating existing package ...")
        subprocess.call(['osc', 'up'])
    local_uri = target_dir + '/' + spec.source.rsplit("/", 2)[-1]
    print('Processing ' + target_dir + ' ...')
    with open(target_dir + '/_service', mode = "w") as srv_file:
      spec.generate_service(srv_file)
    pack_name = PACKAGE_PREFIX.format(spec.name)
    with open(target_dir + '/' + pack_name + ".spec", mode = "w") as rpmSpec:
      spec.render(rpmSpec)
    with open(target_dir + '/' + pack_name + "-rpmlintrc", mode = "w") as lintFile:
      lintFile.write("""setBadness('devel-file-in-non-devel-package', 0)
setBadness('shlib-policy-name-error', 0)""")
    subprocess.check_call(['osc', 'addremove'])
    with subprocess.Popen(["osc", "st"], stdout = subprocess.PIPE) as status:
      if status == '':
        continue
    subprocess.check_call(['osc', 'ci', '-m', '"ros2spec automated check-in"'])
      