import subprocess
import sys
import re
import xml.etree.ElementTree as etree

PACKAGE_PREFIX = "ros-{0}"

class DependencyStateStore:
  def __init__(self, buildtool_depends, build_depends, run_depends):
    self.unmarked_build = build_depends.union(buildtool_depends)
    self.unmarked_run = run_depends
    self.marked_build = set()
    self.marked_run = set()

  def _prefix_marked_packages(self, packages):
    return set(map(lambda pkg: PACKAGE_PREFIX.format(pkg),
                   packages))

  def build_packages(self):
    return self.unmarked_build.union(self._prefix_marked_packages(self.marked_build))
  def run_packages(self):
    return self.unmarked_run.union(self._prefix_marked_packages(self.marked_run))

  def __str__(self):
    return self.build_packages().union(self.run_packages()).__str__()

  def mark(self, package_name):
    if package_name in self.unmarked_build:
      self.unmarked_build.discard(package_name)
      self.marked_build.add(package_name)
    if package_name in self.unmarked_run:
      self.unmarked_run.discard(package_name)
      self.marked_run.add(package_name)

class RPMSpec:
  def __init__(self, xmlPath, wsPath):
    tree = etree.parse(xmlPath)
    root = tree.getroot()
    self.name = root.find('name').text
    self.version = root.find('version').text
    self.url = root.find('url').text
    self.description = re.sub('\s+', ' ', root.find('description').text)
    self.summary = self.description.split(".", 1)[0]
    self.license = root.find('license').text
    with subprocess.Popen(['wstool', 'info', '-t', wsPath, '--only', 'cur_uri', self.name], stdout=subprocess.PIPE, universal_newlines=True) as provided_source:
      self.source = provided_source.stdout.readline()
    def elementText(element):
      return element.text
    self.dependencies = DependencyStateStore(set(map(elementText,
                                                     root.findall('buildtool_depend'))),
                                             set(map(elementText,
                                                     root.findall('build_depend'))),
                                             set(map(elementText,
                                                     root.findall('run_depend'))))
    with subprocess.Popen(["wstool", "info", "-t", wsPath, "--only", "localname"], stdout=subprocess.PIPE, universal_newlines=True) as provided_results:
      for provided_result in provided_results.stdout:
        provided = provided_result.rstrip()
        self.dependencies.mark(provided)

  def render(self, stream):
    header_template = """
Name:	{pkg_name}
Version:	{version}
Release:	0
License:	{license}
Summary:	{summary}
Url:	{url}
Group:	Productivity/Scientific/Other
Source0:	{source}
Source1:	rosManifestParser.py
Source2:	{pkg_name}-rpmlintrc

BuildRequires:  python-devel
BuildRequires:  gcc-c++
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
CMAKE_PREFIX_PATH=/usr catkin_make -DSETUPTOOLS_ARG_EXTRA="" -DCMAKE_INSTALL_PREFIX=/usr

%install
catkin_make install DESTDIR=%{{?buildroot}}
rm %{{?buildroot}}/usr/.catkin %{{?buildroot}}/usr/.rosinstall \
   %{{?buildroot}}/usr/env.sh %{{?buildroot}}/usr/_setup_util.py \
   %{{?buildroot}}/usr/setup*
mkdir %{{?buildroot}}/usr/share/pkgconfig
mv %{{?buildroot}}/usr/lib/pkgconfig/{name}.pc %{{?buildroot}}/usr/share/pkgconfig/
rmdir %{{?buildroot}}/usr/lib/pkgconfig
python %{{SOURCE1}} {name} build/install_manifest.txt

%files -f ros_install_manifest
%defattr(-,root,root)

%changelog
"""
    stream.write(body.format(name=self.name))

if __name__ == '__main__':
  xmlPath = sys.argv[1]
  wsPath = sys.argv[2]
  destination = sys.argv[3]
  spec = RPMSpec(xmlPath, wsPath)
  with open("{0}/{1}.spec".format(destination, PACKAGE_PREFIX.format(spec.name)), mode="w") as rpmSpec, open("{0}/{1}-rpmlintrc".format(destination, PACKAGE_PREFIX.format(spec.name)), mode="w") as lintFile, open("{0}/rosManifestParser.py".format(destination), mode="w") as parserFile:
    spec.render(rpmSpec)
    lintFile.write("setBadness('devel-file-in-non-devel-package', 0)")
    parserFile.write("""
import re
import sys

def is_valid_manifest_entry(manifest_entry):
  blacklist = set(map(re.compile,
                       {"/usr/\.catkin", "/usr/_setup_util.py",
                        "/usr/env.sh", "/usr/setup\.+?",
                        "/usr/\.rosinstall", "/usr/lib/pkgconfig*?",
                        "/usr/share/*"}))
  for blacklist_entry in blacklist:
    if blacklist_entry.match(manifest_entry) != None:
      return False
  return True

def extract_manifest(manifestPath):
  manifest = set()
  with open(manifestPath) as manifestFile:
    for entry in manifestFile:
      if is_valid_manifest_entry(entry):
        manifest.add(entry.rstrip())
  return manifest

def add_directories(manifest):
  root = "/usr"
  toplevels = {"/usr/share", "/usr/lib"}
  directory_manifest = set()
  for entry in manifest:
    parsed_directory_structure = root
    if entry[0] != '%':
      directory_structure = entry[len(root):].split("/")
      for folder in directory_structure[1:-1]:
        parsed_directory_structure = parsed_directory_structure + "/" + folder
        if parsed_directory_structure not in toplevels:
          directory_manifest.add("%dir " + parsed_directory_structure)
  return manifest.union(directory_manifest)

if __name__ == '__main__':
  name = sys.argv[1]
  manifestPath = sys.argv[2]
  manifest = extract_manifest(manifestPath)
  manifest.add("%{{_datadir}}/{0}".format(name))
  manifest.add("%{{python_sitelib}}/{0}*".format(name))
  manifest.add("%{{_datadir}}/pkgconfig/{0}.pc".format(name))
  with open("ros_install_manifest", mode="w") as manifestFile:
    manifestFile.writelines(map(lambda str : str+"\\n", add_directories(manifest)))
""")
