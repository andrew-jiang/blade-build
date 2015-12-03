# Copyright (c) 2013 Tencent Inc.
# All rights reserved.
#
# Author: CHEN Feng <phongchen@tencent.com>
# Created: Jun 26, 2013


"""
Implement java_library, java_binary, java_test and java_fat_library
"""


import os
import re

import blade
import blade_util
import build_rules
import configparse
import console
import maven

from blade_util import var_to_list
from target import Target


class MavenJar(Target):
    """MavenJar"""
    def __init__(self, name, id, is_implicit_added):
        Target.__init__(self, name, 'maven_jar', [], [], blade.blade, {})
        self.data['id'] = id
        if is_implicit_added:
            self.key = ('#', name)
            self.fullname = '%s:%s' % self.key
            self.path = '#'

    def _get_java_pack_deps(self):
        deps = self.data.get('maven_deps', [])
        return [], deps

    def scons_rules(self):
        maven_cache = maven.MavenCache.instance()
        binary_jar = maven_cache.get_jar_path(self.data['id'])
        if binary_jar:
            self.data['binary_jar'] = binary_jar
            deps_path = maven_cache.get_jar_deps_path(self.data['id'])
            if deps_path:
                self.data['maven_deps'] = deps_path.split(':')


class JavaTargetMixIn(object):
    """
    This mixin includes common java methods
    """
    def _add_hardcode_java_library(self, deps):
        """Add hardcode dep list to key's deps. """
        for dep in deps:
            if maven.is_valid_id(dep):
                self._add_maven_dep(dep)
                continue
            dkey = self._unify_dep(dep)
            if dkey not in self.deps:
                self.deps.append(dkey)
            if dkey not in self.expanded_deps:
                self.expanded_deps.append(dkey)

    def _add_maven_dep(self, id):
        name = blade_util.regular_variable_name(id).replace(':', '_')
        key = ('#', name)
        if not key in self.target_database:
            target = MavenJar(name, id, is_implicit_added=True)
            blade.blade.register_target(target)
        self.deps.append(key)
        self.expanded_deps.append(key)
        return key

    def _collect_maven_dep_ids(self):
        maven_dep_ids = set()
        for dkey in self.deps:
            dep = self.target_database[dkey]
            if dep.type == 'maven_jar':
                id = dep.data.get('id')
                if id:
                    maven_dep_ids.add(id)
        return maven_dep_ids

    def _filter_deps(self, deps):
        filtered_deps = []
        filterouted_deps = []
        for dep in deps:
            if maven.is_valid_id(dep):
                filterouted_deps.append(dep)
            else:
                filtered_deps.append(dep)
        return filtered_deps, filterouted_deps

    def _unify_java_deps(self, deps):
        dkeys = []
        for dep in deps:
            if maven.is_valid_id(dep):
                dkey = self._add_maven_dep(dep)
                dkeys.append(dkey)
                continue
            dkey = self._unify_dep(dep)
            dkeys.append(dkey)
        return dkeys

    def _set_pack_exclusions(self, exclusions):
        exclusions = var_to_list(exclusions)
        self.data['exclusions'] = []
        for exclusion in exclusions:
            exclusion = self._unify_dep(exclusion)
            self.data['exclusions'].append(exclusion)


    def _get_classes_dir(self):
        """Return path of classes dir. """
        return self._target_file_path() + '.classes'

    def __extract_dep_jars(self, dkey, dep_jar_vars, dep_jars):
        dep = self.target_database[dkey]
        jar = dep.data.get('jar_var')
        if jar:
            dep_jar_vars.append(jar)
        else:
            jar = dep.data.get('binary_jar')
            if jar:
                dep_jars.append(jar)

    def __get_deps(self, deps):
        """
        Return a tuple of (scons vars, jars)
        """
        dep_jar_vars = []
        dep_jars = []
        for d in deps:
            self.__extract_dep_jars(d, dep_jar_vars, dep_jars)
        return dep_jar_vars, dep_jars

    def __get_exported_deps(self, deps):
        """
        Return a tuple of (scons vars, jars)
        """
        dep_jar_vars = []
        dep_jars = []
        for dkey in deps:
            dep = self.target_database[dkey]
            exported_deps = dep.data.get('exported_deps', [])
            for edkey in exported_deps:
                self.__extract_dep_jars(edkey, dep_jar_vars, dep_jars)
        return dep_jar_vars, dep_jars

    def __get_maven_transitive_deps(self, deps):
        """
        Return a list of maven jars stored within local repository.
        These jars are transitive dependencies of maven_jar target.
        """
        maven_jars = []
        for key in deps:
            dep = self.target_database[key]
            if dep.type == 'maven_jar':
                maven_jars += dep.data.get('maven_deps', [])
        return maven_jars

    def _detect_maven_conflicted_deps(self, dep_jars):
        """
        Maven dependencies might have conflict: same group and artifact
        but different version. Select higher version by default unless
        a specific version of maven dependency is specified as a direct
        dependency of the target
        """
        dep_jars, conflicted_jars = set(dep_jars), set()
        maven_dep_ids = self._collect_maven_dep_ids()
        maven_jar_dict = {}  # (group, artifact) -> (version, name, jar)
        maven_repo = '.m2/repository/'
        for dep_jar in dep_jars:
            if maven_repo not in dep_jar:
                continue
            parts = dep_jar[dep_jar.find(maven_repo) + len(maven_repo):].split('/')
            if len(parts) < 4:
                continue
            name, version, artifact, group = (parts[-1], parts[-2],
                                              parts[-3], '.'.join(parts[:-3]))
            key = (group, artifact)
            id = ':'.join((group, artifact, version))
            if key in maven_jar_dict:
                old_value = maven_jar_dict[key]
                old_id = ':'.join((group, artifact, old_value[0]))
                if old_id in maven_dep_ids:
                    conflicted_jars.add(dep_jar)
                elif id in maven_dep_ids or version > old_value[0]:
                    conflicted_jars.add(old_value[2])
                    maven_jar_dict[key] = (version, name, dep_jar)
                else:
                    conflicted_jars.add(dep_jar)
                value = maven_jar_dict[key]
                console.warning('Detect maven dependency conflict between %s '
                                'and %s in %s. Use %s' % (id,
                                ':'.join([key[0], key[1], old_value[0]]),
                                self.fullname,
                                ':'.join([key[0], key[1], value[0]])))
            else:
                maven_jar_dict[key] = (version, name, dep_jar)

        dep_jars -= conflicted_jars
        return sorted(list(dep_jars))

    def _get_compile_deps(self):
        dep_jar_vars, dep_jars = self.__get_deps(self.deps)
        exported_dep_jar_vars, exported_dep_jars = self.__get_exported_deps(self.deps)
        dep_jars += self.__get_maven_transitive_deps(self.deps)
        dep_jar_vars = sorted(list(set(dep_jar_vars + exported_dep_jar_vars)))
        dep_jars = self._detect_maven_conflicted_deps(dep_jars + exported_dep_jars)
        return dep_jar_vars, dep_jars

    def _get_test_deps(self):
        dep_jar_vars, dep_jars = self.__get_deps(self.expanded_deps)
        dep_jars += self.__get_maven_transitive_deps(self.expanded_deps)
        dep_jar_vars = sorted(list(set(dep_jar_vars)))
        dep_jars = self._detect_maven_conflicted_deps(dep_jars)
        return dep_jar_vars, dep_jars

    def _get_pack_deps(self):
        """
        Recursively scan direct dependencies and exclude provided dependencies.
        """
        deps = set(self.deps)
        provided_deps = self.data.get('provided_deps', [])
        for provided_dep in provided_deps:
            deps.discard(provided_dep)
        dep_jar_vars, dep_jars = self.__get_deps(deps)

        for dep in deps:
            dep = self.target_database[dep]
            jar_vars, jars = dep._get_java_pack_deps()
            dep_jar_vars += jar_vars
            dep_jars += jars

        dep_jar_vars, dep_jars = set(dep_jar_vars), set(dep_jars)
        exclusions = self.data.get('exclusions', [])
        if exclusions:
            exclude_jar_vars, exclude_jars = self.__get_deps(exclusions)
            for exclude_jar_var in exclude_jar_vars:
                dep_jar_vars.discard(exclude_jar_var)
            for exclude_jar in exclude_jars:
                dep_jars.discard(exclude_jar)

        return sorted(list(dep_jar_vars)), sorted(list(dep_jars))

    def _get_java_package_name(self, file_name):
        """Get the java package name from proto file if it is specified. """
        if not os.path.isfile(file_name):
            return ''
        package_pattern = '^\s*package\s+([\w.]+)'
        content = open(file_name).read()
        m = re.search(package_pattern, content, re.MULTILINE)
        if m:
            return m.group(1)

        return ''

    def _java_sources_paths(self, srcs):
        path = set()
        segs = [
            'src/main/java',
            'src/test/java',
            'src/java/',
        ]
        for src in srcs:
            for seg in segs:
                pos = src.find(seg)
                if pos > 0:
                    path.add(src[:pos + len(seg)])
                    continue
            package = self._get_java_package_name(src)
            if package:
                package = package.replace('.', '/') + '/'
                pos = src.find(package)
                if pos > 0:
                    path.add(src[:pos])
                    continue

        return list(path)

    def _generate_java_versions(self):
        java_config = configparse.blade_config.get_config('java_config')
        version = java_config['version']
        source_version = java_config.get('source_version', version)
        target_version = java_config.get('target_version', version)
        # JAVAVERSION must be set because scons need it to deduce class names
        # from java source, and the default value '1.5' is too low.
        blade_java_version = version or '1.6'
        self._write_rule('%s.Replace(JAVAVERSION="%s")' % (
            self._env_name(), blade_java_version))
        if source_version:
            self._write_rule('%s.Append(JAVACFLAGS="-source %s")' % (
                self._env_name(), source_version))
        if target_version:
            self._write_rule('%s.Append(JAVACFLAGS="-target %s")' % (
                self._env_name(), target_version))

    def _generate_java_source_encoding(self):
        source_encoding = self.data.get('source_encoding')
        if source_encoding is None:
            config = configparse.blade_config.get_config('java_config')
            source_encoding = config['source_encoding']
        if source_encoding:
            self._write_rule('%s.Append(JAVACFLAGS="-encoding %s")' % (
                self._env_name(), source_encoding))

    def _generate_java_sources_paths(self, srcs):
        path = self._java_sources_paths(srcs)
        if path:
            env_name = self._env_name()
            self._write_rule('%s.Append(JAVASOURCEPATH=%s)' % (env_name, path))

    def _generate_java_classpath(self, dep_jar_vars, dep_jars):
        env_name = self._env_name()
        for dep_jar_var in dep_jar_vars:
            # Can only append one by one here, maybe a scons bug.
            # Can only append as string under scons 2.1.0, maybe another bug or defect.
            self._write_rule('%s.Append(JAVACLASSPATH=str(%s[0]))' % (
                env_name, dep_jar_var))
        if dep_jars:
            self._write_rule('%s.Append(JAVACLASSPATH=%s)' % (env_name, dep_jars))

    def _generate_java_depends(self, var_name, dep_jar_vars, dep_jars):
        self._write_rule('%s.Depends(%s, [%s])' % (
            self._env_name(), var_name, ','.join(dep_jar_vars)))

    def _generate_java_classes(self, var_name, srcs):
        env_name = self._env_name()

        self._generate_java_sources_paths(srcs)
        dep_jar_vars, dep_jars = self._get_compile_deps()
        self._generate_java_classpath(dep_jar_vars, dep_jars)
        classes_dir = self._get_classes_dir()
        self._write_rule('%s = %s.Java(target="%s", source=%s)' % (
                var_name, env_name, classes_dir, srcs))
        self._generate_java_depends(var_name, dep_jar_vars, dep_jars)
        self._write_rule('%s.Clean(%s, "%s")' % (env_name, var_name, classes_dir))
        return var_name

    def _generate_resources(self):
        resources = self.data['resources']
        if not resources:
            return ''
        resources = [self._source_file_path(res) for res in resources]
        env_name = self._env_name()
        var_name = self._var_name('resources')
        resources_dir = self._target_file_path() + '.resources'
        self._write_rule('%s = %s.JavaResource(target="%s", source=%s)' % (
            var_name, env_name, resources_dir, resources))
        self._write_rule('%s.Clean(%s, "%s")' % (env_name, var_name, resources_dir))
        return var_name

    def _generate_generated_java_jar(self, var_name, srcs):
        env_name = self._env_name()
        self._write_rule('%s = %s.GeneratedJavaJar(target="%s" + top_env["JARSUFFIX"], source=[%s])' % (
            var_name, env_name, self._target_file_path(), ','.join(srcs)))
        self.data['jar_var'] = var_name

    def _generate_java_jar(self, var_name, classes_var, resources_var):
        env_name = self._env_name()
        sources = []
        if classes_var:
            sources.append(classes_var)
        if resources_var:
            sources.append(resources_var)
        if sources:
            self._write_rule('%s = %s.BladeJavaJar(target="%s", source=[%s])' % (
                var_name, env_name,
                self._target_file_path() + '.jar', ','.join(sources)))
            self.data['jar_var'] = var_name


class JavaTarget(Target, JavaTargetMixIn):
    """A java jar target subclass.

    This class is the base of all java targets.

    """
    def __init__(self,
                 name,
                 type,
                 srcs,
                 deps,
                 resources,
                 source_encoding,
                 warnings,
                 kwargs):
        """Init method.

        Init the java jar target.

        """
        srcs = var_to_list(srcs)
        deps, mvn_deps = self._filter_deps(var_to_list(deps))
        resources = var_to_list(resources)

        Target.__init__(self,
                        name,
                        type,
                        srcs,
                        deps,
                        blade.blade,
                        kwargs)
        self.data['resources'] = resources
        self.data['source_encoding'] = source_encoding
        if warnings is not None:
            self.data['warnings'] = var_to_list(warnings)
        for dep in mvn_deps:
            self._add_maven_dep(dep)

    def _prepare_to_generate_rule(self):
        """Should be overridden. """
        self._check_deprecated_deps()
        self._clone_env()
        self._generate_java_versions()
        self._generate_java_source_encoding()
        warnings = self.data.get('warnings')
        if warnings is None:
            config = configparse.blade_config.get_config('java_config')
            warnings = config['warnings']
        if warnings:
            self._write_rule('%s.Append(JAVACFLAGS=%s)' % (
                self._env_name(), warnings))

    def _get_java_pack_deps(self):
        return self._get_pack_deps()

    def _generate_classes(self):
        if not self.srcs:
            return None
        var_name = self._var_name('classes')
        srcs = [self._source_file_path(src) for src in self.srcs]
        return self._generate_java_classes(var_name, srcs)

    def _generate_jar(self):
        var_name = self._var_name('jar')
        classes_var = self._generate_classes()
        resources_var = self._generate_resources()
        self._generate_java_jar(var_name, classes_var, resources_var)


class JavaLibrary(JavaTarget):
    """JavaLibrary"""
    def __init__(self, name, srcs, deps, resources, source_encoding, warnings,
                 prebuilt, binary_jar, exported_deps, provided_deps, kwargs):
        type = 'java_library'
        if prebuilt:
            type = 'prebuilt_java_library'
        exported_deps = var_to_list(exported_deps)
        provided_deps = var_to_list(provided_deps)
        all_deps = var_to_list(deps) + exported_deps + provided_deps
        JavaTarget.__init__(self, name, type, srcs, all_deps, resources,
                            source_encoding, warnings, kwargs)
        self.data['exported_deps'] = self._unify_java_deps(exported_deps)
        self.data['provided_deps'] = self._unify_java_deps(provided_deps)
        if prebuilt:
            if not binary_jar:
                self.data['binary_jar'] = name + '.jar'
            self.data['binary_jar'] = self._source_file_path(binary_jar)

    def scons_rules(self):
        if self.type != 'prebuilt_java_library':
            self._prepare_to_generate_rule()
            self._generate_jar()


class JavaBinary(JavaTarget):
    """JavaBinary"""
    def __init__(self, name, srcs, deps, resources, source_encoding,
                 warnings, main_class, exclusions, kwargs):
        JavaTarget.__init__(self, name, 'java_binary', srcs, deps, resources,
                            source_encoding, warnings, kwargs)
        self.data['main_class'] = main_class
        self.data['run_in_shell'] = True
        if exclusions:
            self._set_pack_exclusions(exclusions)

    def scons_rules(self):
        self._prepare_to_generate_rule()
        self._generate_jar()
        dep_jar_vars, dep_jars = self._get_pack_deps()
        dep_jars = self._detect_maven_conflicted_deps(dep_jars)
        self._generate_wrapper(self._generate_one_jar(dep_jar_vars, dep_jars))

    def _get_all_depended_jars(self):
        return []

    def _generate_one_jar(self, dep_jar_vars, dep_jars):
        var_name = self._var_name('onejar')
        jar_vars = []
        if self.data.get('jar_var'):
            jar_vars = [self.data.get('jar_var')]
        jar_vars.extend(dep_jar_vars)
        self._write_rule('%s = %s.OneJar(target="%s", source=[Value("%s")] + [%s] + %s)' % (
            var_name, self._env_name(),
            self._target_file_path() + '.one.jar', self.data['main_class'],
            ','.join(jar_vars), dep_jars))
        return var_name

    def _generate_wrapper(self, onejar):
        var_name = self._var_name()
        self._write_rule('%s = %s.JavaBinary(target="%s", source=%s)' % (
            var_name, self._env_name(), self._target_file_path(), onejar))


class JavaTest(JavaBinary):
    """JavaTarget"""
    def __init__(self, name, srcs, deps, resources, source_encoding,
                 warnings, main_class, testdata, kwargs):
        java_test_config = configparse.blade_config.get_config('java_test_config')
        JavaBinary.__init__(self, name, srcs, deps, resources,
                            source_encoding, warnings, main_class, None, kwargs)
        self.type = 'java_test'
        self.data['testdata'] = var_to_list(testdata)

    def scons_rules(self):
        self._prepare_to_generate_rule()
        self._generate_jar()
        dep_jar_vars, dep_jars = self._get_test_deps()
        self._generate_wrapper(self._generate_one_jar(dep_jar_vars, dep_jars),
                               dep_jar_vars)

    def _generate_wrapper(self, onejar, dep_jar_vars):
        var_name = self._var_name()
        self._write_rule('%s = %s.JavaTest(target="%s", source=[%s, %s] + [%s])' % (
            var_name, self._env_name(), self._target_file_path(),
            onejar, self.data['jar_var'], ','.join(dep_jar_vars)))


class JavaFatLibrary(JavaTarget):
    """JavaFatLibrary"""
    def __init__(self, name, srcs, deps, resources, source_encoding,
                 warnings, exclusions, kwargs):
        JavaTarget.__init__(self, name, 'java_fat_library', srcs, deps,
                            resources, source_encoding, warnings, kwargs)
        if exclusions:
            self._set_pack_exclusions(exclusions)

    def scons_rules(self):
        self._prepare_to_generate_rule()
        self._generate_jar()
        dep_jar_vars, dep_jars = self._get_pack_deps()
        dep_jars = self._detect_maven_conflicted_deps(dep_jars)
        self._generate_fat_jar(dep_jar_vars, dep_jars)

    def _generate_fat_jar(self, dep_jar_vars, dep_jars):
        var_name = self._var_name('fatjar')
        jar_vars = []
        if self.data.get('jar_var'):
            jar_vars = [self.data.get('jar_var')]
        jar_vars.extend(dep_jar_vars)
        self._write_rule('%s = %s.FatJar(target="%s", source=[%s] + %s)' % (
            var_name, self._env_name(),
            self._target_file_path() + '.fat.jar',
            ','.join(jar_vars), dep_jars))


def maven_jar(name, id):
    target = MavenJar(name, id, is_implicit_added=False)
    blade.blade.register_target(target)


def java_library(name,
                 srcs=[],
                 deps=[],
                 resources=[],
                 source_encoding=None,
                 warnings=None,
                 prebuilt=False,
                 binary_jar='',
                 exported_deps=[],
                 provided_deps=[],
                 **kwargs):
    """Define java_library target. """
    target = JavaLibrary(name,
                         srcs,
                         deps,
                         resources,
                         source_encoding,
                         warnings,
                         prebuilt,
                         binary_jar,
                         exported_deps,
                         provided_deps,
                         kwargs)
    blade.blade.register_target(target)


def java_binary(name,
                main_class,
                srcs=[],
                deps=[],
                resources=[],
                source_encoding=None,
                warnings=None,
                exclusions=[],
                **kwargs):
    """Define java_binary target. """
    target = JavaBinary(name,
                        srcs,
                        deps,
                        resources,
                        source_encoding,
                        warnings,
                        main_class,
                        exclusions,
                        kwargs)
    blade.blade.register_target(target)


def java_test(name,
              srcs=[],
              deps=[],
              resources=[],
              source_encoding=None,
              warnings=None,
              main_class = 'org.junit.runner.JUnitCore',
              testdata=[],
              **kwargs):
    """Define java_test target. """
    target = JavaTest(name,
                      srcs,
                      deps,
                      resources,
                      source_encoding,
                      warnings,
                      main_class,
                      testdata,
                      kwargs)
    blade.blade.register_target(target)


def java_fat_library(name,
                     srcs=[],
                     deps=[],
                     resources=[],
                     source_encoding='',
                     warnings=None,
                     exclusions=[],
                     **kwargs):
    """Define java_fat_library target. """
    target = JavaFatLibrary(name,
                            srcs,
                            deps,
                            resources,
                            source_encoding,
                            warnings,
                            exclusions,
                            kwargs)
    blade.blade.register_target(target)


build_rules.register_function(maven_jar)
build_rules.register_function(java_binary)
build_rules.register_function(java_library)
build_rules.register_function(java_test)
build_rules.register_function(java_fat_library)
