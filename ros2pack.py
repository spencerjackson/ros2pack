import subprocess
import sys
import xml.etree.ElementTree as etree

PACKAGE_PREFIX = "ros-"

class DependencyStateStore:
  def __init__(self, buildtool_depends, build_depends, run_depends):
    self.unmarked_build = build_depends.union(buildtool_depends)
    self.unmarked_run = run_depends
    self.marked_build = set()
    self.marked_run = set()

  def __str__(self):
    packages = self.unmarked_build.union(self.unmarked_run)
    packages = packages.union(map(lambda pkg: PACKAGE_PREFIX + pkg,
                                  self.marked_build.union(self.marked_run)))

    return packages.__str__()

  def mark(self, package_name):
    if package_name in self.unmarked_build:
      self.unmarked_build.discard(package_name)
      self.marked_build.add(package_name)
    if package_name in self.unmarked_run:
      self.unmarked_run.discard(package_name)
      self.marked_run.add(package_name)

def parsePackage(xmlPath):
  tree = etree.parse(xmlPath)
  root = tree.getroot()
  def elementText(element):
    return element.text
  return DependencyStateStore(set(map(elementText,
                                  root.findall('buildtool_depend'))),
                              set(map(elementText,
                                  root.findall('build_depend'))),
                              set(map(elementText,
                                  root.findall('run_depend'))))

def identifyProvided(wsPath, dependencyStore):
  with subprocess.Popen(["wstool", "info", "-t", wsPath, "--only", "localname"], stdout=subprocess.PIPE, universal_newlines=True) as provided_results:
    for provided_result in provided_results.stdout:
      provided = provided_result.rstrip()
      dependencyStore.mark(provided)
  return dependencyStore

if __name__ == '__main__':
  xmlPath = sys.argv[1]
  wsPath = sys.argv[2]
  dependencies = parsePackage(xmlPath)
  print(identifyProvided(wsPath, dependencies))
