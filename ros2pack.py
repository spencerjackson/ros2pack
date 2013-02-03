import subprocess
import sys
import re
import os.path
import xml.etree.ElementTree as etree
import argparse

PACKAGE_PREFIX = "ros-{0}"

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
      with subprocess.Popen(['rosdep', 'resolve', self._name], stdout=subprocess.PIPE, universal_newlines=True) as rosdep_stream:
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

def RPMSpec_factory(packagePath, wsPath):
  tree = etree.parse(packagePath+"/package.xml")
  root = tree.getroot()
  name = root.find('name').text
  version = root.find('version').text
  url = root.find('url').text
  description = re.sub('\s+', ' ', root.find('description').text)
  summary = description.split(".", 1)[0]
  license = root.find('license').text
  with subprocess.Popen(['wstool', 'info', '-t', wsPath, '--only', 'cur_uri', name], stdout=subprocess.PIPE, universal_newlines=True) as provided_source:
    source = provided_source.stdout.readline()
  def elementText(element):
    return element.text
  dependencies = DependencyStore(list(map(elementText,
                                              root.findall('buildtool_depend'))),
                                      list(map(elementText,
                                              root.findall('build_depend'))),
                                      list(map(elementText,
                                              root.findall('run_depend'))))
  with subprocess.Popen(["wstool", "info", "-t", wsPath, "--only", "localname"], stdout=subprocess.PIPE, universal_newlines=True) as provided_results:
    for provided_result in provided_results.stdout:
      provided = provided_result.rstrip()
      dependencies.mark(provided)
  has_python = os.path.isfile(packagePath + "/setup.py")
  return RPMSpec(name, version, source, url, description, summary, license, dependencies, has_python)

class RPMSpec:
  def __init__(self, name, version, source, url, description, summary, license, dependencies, has_python):
    self.name = name
    self.version = version
    self.source = source
    self.url = url
    self.description = description
    self.summary = summary
    self.license = license
    self.dependencies = dependencies
    self.has_python = has_python

  def render(self, stream):
    header_template = """%define __pkgconfig_path {{""}}

Name:	{pkg_name}
Version:	{version}
Release:	0
License:	{license}
Summary:	{summary}
Url:	{url}
Group:	Productivity/Scientific/Other
Source0:	{source}
Source1:	{pkg_name}-rpmlintrc

BuildRequires:  python-devel
BuildRequires:  gcc-c++
BuildRequires:  python-rosmanifestparser
"""
    stream.write(header_template.format(pkg_name=PACKAGE_PREFIX.format(self.name),
                                        version=self.version, license=self.license,
                                        summary=self.summary, url=self.url,
                                        source=self.source))

    for build_dependency in self.dependencies.build_packages():
      stream.write("BuildRequires:	{0}\n".format(build_dependency))
    for run_dependency in self.dependencies.run_packages():
      stream.write("Requires:	{0}\n".format(run_dependency))
    stream.write("\n%description\n{0}\n".format(self.description))

    body = """
%prep
%setup -q -c -n workspace
mv * {name}
mkdir src
mv {name} src

%build
CMAKE_PREFIX_PATH=/usr catkin_make -DSETUPTOOLS_DEB_LAYOUT="OFF" -DCMAKE_INSTALL_PREFIX=/usr

%install
catkin_make install DESTDIR=%{{?buildroot}}
rm %{{?buildroot}}/usr/.catkin %{{?buildroot}}/usr/.rosinstall \
   %{{?buildroot}}/usr/env.sh %{{?buildroot}}/usr/_setup_util.py \
   %{{?buildroot}}/usr/setup*
mkdir %{{?buildroot}}/usr/share/pkgconfig
mv %{{?buildroot}}/usr/lib/pkgconfig/{name}.pc %{{?buildroot}}/usr/share/pkgconfig/
rmdir %{{?buildroot}}/usr/lib/pkgconfig
rosmanifestparser {name} build/install_manifest.txt %{{?buildroot}} {has_python}

%files -f ros_install_manifest
%defattr(-,root,root)

%changelog
"""
    stream.write(body.format(name=self.name, has_python=self.has_python))

if __name__ == '__main__':
  parser = argparse.ArgumentParser(description="Generate RPM spec files from ROS packages")
  parser.add_argument('workspace', type=str,
                      help='path to the root of the workspace')
  parser.add_argument('--packages', type=str, dest='packages', nargs='+',
                       help='process only the specifed packages')
  parser.add_argument('destination', type=str,
                      help='path to the spec root')
  args = parser.parse_args()
  if args.packages == None:
    packages = [name for name in os.listdir(args.workspace+"/src") if os.path.isdir(args.workspace+"/src/"+name)]
  else:
    packages = args.packages
  for package in packages:
    spec = RPMSpec_factory(args.workspace+"/src/"+package, args.workspace+"/src")
    target_dir = args.destination+"/"+PACKAGE_PREFIX.format(package)
    if not os.path.exists(target_dir):
      os.makedirs(target_dir)
    with open("{0}/{1}.spec".format(target_dir, PACKAGE_PREFIX.format(spec.name)), mode="w") as rpmSpec, open("{0}/{1}-rpmlintrc".format(target_dir, PACKAGE_PREFIX.format(spec.name)), mode="w") as lintFile:
      spec.render(rpmSpec)
      lintFile.write("""setBadness('devel-file-in-non-devel-package', 0)
setBadness('shlib-policy-name-error', 0)""")
