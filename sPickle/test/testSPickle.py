#
# -*- coding: utf-8 -*-
#
# Copyright (c) 2011 by science+computing ag
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

from __future__ import absolute_import

from unittest import TestCase, skipIf

import pickletools
from StringIO import StringIO
import cStringIO
import pickle
import types
import sys
import imp
import traceback
import thread
import functools
# a pure python module used for testing
import tabnanny
import os.path
import socket
import collections
import logging
import operator
import weakref

try:
    import gtk
except ImportError:
    gtk = None

logging.basicConfig(level=logging.INFO)


from .. import _sPickle
from . import wf_module 


# Various test objects

def aFunction():
    """A plain global function """
    return True
def aFunction2():
    """A plain global function 2"""
    return True
def aFunction3():
    """A plain global function 3"""
    return True

def functionWithArg(arg):
    """A function that takes an argument"""
    return bool(arg)

decoratedFunction = functools.partial(functionWithArg, 4711)
functools.update_wrapper(decoratedFunction, functionWithArg)
del functionWithArg


class ModuleWithOrderedDict(types.ModuleType):
    __slots__=("__od__",)
    def __new__(cls, name, *doc):
        m = super(ModuleWithOrderedDict, cls).__new__(cls, name, *doc)
        od = collections.OrderedDict()
        od["__name__"] = name
        od["__doc__"] = doc[0] if len(doc) > 0 else None
        object.__setattr__(m, "__od__", od)
        return m
    def __setattr__(self, name, value):
        object.__getattribute__(self, "__od__")[name] = value
        super(ModuleWithOrderedDict, self).__setattr__(name, value)
    def __getattribute__(self, name):
        if name in ("__slots__", "__od__"):
            raise AttributeError("%r object has no attribute %r" % (type(self).__name__, name))
        if name == "__dict__":           
            return object.__getattribute__(self, "__od__")
        return object.__getattribute__(self, name)
    
modForPartiallyUnpickleable = ModuleWithOrderedDict("modForPartiallyUnpickleable")
sys.modules["modForPartiallyUnpickleable"] = modForPartiallyUnpickleable
setattr(modForPartiallyUnpickleable, _sPickle.MODULE_TO_BE_PICKLED_FLAG_NAME, True)
modForPartiallyUnpickleable.True = True

# A function, within modForPartiallyUnpickleable
# This triggers a problem in pickle.Pickler.save_function
partiallyUnpickleableFunction = types.FunctionType(aFunction.func_code, 
                                                   modForPartiallyUnpickleable.__dict__)
modForPartiallyUnpickleable.aFunction = partiallyUnpickleableFunction

class IntentionallyUnpicleableError(pickle.PicklingError):
    pass
class UnpickleableClass(object):
    def __reduce_ex__(self, proto):
        raise IntentionallyUnpicleableError("Intentionally unpickleable")
modForPartiallyUnpickleable.unpickleable = UnpickleableClass()
del modForPartiallyUnpickleable

class PlainClass(object):
    def __init__(self, state = None):
        self.state = state 
    
    def isOk(self):
        return self.state == "OK"
    
class PlainClassicClass:
    def __init__(self, state = None):
        self.state = state 
    
    def isOk(self):
        return self.state == "OK"
    
        
def ClassWithDictCreatingReduce_reducer(cls, dict):
    obj = cls()
    obj.__dict__ = dict
    return obj
    
class ClassWithDictCreatingReduce(object):
    
    def __reduce_ex__(self, proto):
        return (ClassWithDictCreatingReduce_reducer, (self.__class__, self.__dict__))
    
    def __init__(self, state=None):
        self.state = state
            
    def isOk(self):
        return self.state == "OK"
        
class P:
    
    # this class is not in the module namespace and 
    # therefore the pickler has to serialize the class
    class RecursiveClass(object):
        selfCls = None
        count = 0
        def __init__(self):
            self.count = self.count + 1 
        
        def run(self, test):
            test.assertTrue(self.selfCls is self.__class__)
            test.assertEqual(self.count, 1)
            return True
    RecursiveClass.selfCls = RecursiveClass

class Q(object):
    # this class is not in the module namespace and 
    # therefore the pickler has to serialize the class
    class IndirectRecursiveBaseClass(object):
        pass
    
    class IndirectRecursiveClass(IndirectRecursiveBaseClass):
        count = 0
        def __init__(self):
            self.count = self.count + 1 
        
        def run(self, test):
            test.assertIsInstance(self.refToGlobals, types.DictProxyType)
            test.assertEqual(self.refToGlobals, Q.__dict__)
            test.assertEqual(self.count, 1)
            return True
Q.IndirectRecursiveBaseClass.refToGlobals = Q.__dict__

class R:
    # this class is not in the module namespace and 
    # therefore the pickler has to serialize the class
    class IndirectRecursiveBaseClass(object):
        pass
    
    class IndirectRecursiveClass(IndirectRecursiveBaseClass):
        count = 0
        def __init__(self):
            self.count = self.count + 1 
        
        def run(self, test):
            test.assertTrue(self.refToDict['ref2IndirectRecursiveClass'] is self.__class__)
            test.assertEqual(self.count, 1)
            return True
R.IndirectRecursiveBaseClass.refToDict = { 'ref2IndirectRecursiveClass' : R.IndirectRecursiveClass }

def buildModule(name):
    """Builder for anonymous modules"""
    m = imp.new_module(name)
    # function from another module
    m.foreignFunction = aFunction
    # function of the anonymous module
    exec """
def isOk():
    return foreignFunction()
""" in m.__dict__
    return m

anonymousModule = buildModule("anonymousModule")
anonymousWfModule = buildModule("anonymousWfModule")
setattr(anonymousWfModule, _sPickle.MODULE_TO_BE_PICKLED_FLAG_NAME, True)

class StrangeModuleType(types.ModuleType):
    """Modules of this type have an 'isOk()' function, that is 
    not referenced in the modules dictionary. Therefore this function does not
    get pickled together with its module."""
    
    syntheticFunctions = {}
    
    """A special module type"""
    def __getattr__(self, name):
        if name == "isOk":
            f = self.syntheticFunctions.get(id(self))
            if f is None:
                # create the function, but avoid a reference to the 
                # modules namespace. Just copy the __module__ attribute,
                # to make the synthetic function part of self
                f = eval("lambda : True", {'__name__': self.__module__})
                f.func_name = name
                self.syntheticFunctions[id(self)] = f
            return f
        raise AttributeError(name)
setattr(StrangeModuleType, _sPickle.MODULE_TO_BE_PICKLED_FLAG_NAME, True)

strangeModule = StrangeModuleType("strangeModule")
sys.modules[strangeModule.__name__] = strangeModule
                
        
class TestImportFunctor(object):
    def __init__(self, *modulesToUnimport):
        self.names = []
        self.modulesToUnimport = modulesToUnimport
        self.unimported = set()
            
    def __call__(self, name, *args, **kw):
        self.names.append(name)
        return self.saved_import(name, *args, **kw)
    
    def __enter__(self):
        import __builtin__
        self.saved_import = __builtin__.__import__
        __builtin__.__import__ = self
        self.saved_modules = sys.modules.copy()
        assert self.saved_modules == sys.modules
        
        for m in self.modulesToUnimport:
            if isinstance(m, types.ModuleType):
                name = m.__name__
                if m is not sys.modules.get(name):
                    for k,v in sys.modules.iteritems():
                        if v is m:
                            name = k
                m = name
            if sys.modules.has_key(m):
                self.unimported.add(sys.modules[m])
                del sys.modules[m]
                
        self.pre_modules = sys.modules.copy()
        assert self.pre_modules == sys.modules
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        import __builtin__
        __builtin__.__import__ = self.saved_import
        self.post_modules = sys.modules.copy()
        assert self.post_modules == sys.modules
        sys.modules.clear()
        sys.modules.update(self.saved_modules)
        
        return False
        

class ClassWithHostile__getattr__(object):
    def __init__(self, allow_setstate):
        self.allow_setstate = allow_setstate
    
    def __getattr__(self, name):
        if name == "__setstate__" and self.allow_setstate:
            raise AttributeError(name)
        raise Exception("This __getattr__ always raises an exception")

class ClassWithHostile__getattribute__(object):
    def __init__(self, allow_setstate):
        self.allow_setstate = allow_setstate
    
    def __getattribute__(self, name):
        if name == "__setstate__" and object.__getattribute__(self, "allow_setstate"):
            raise AttributeError(name)
        if name in ("__reduce_ex__", "__reduce__", "__class__"):
            return object.__getattribute__(self, name)
        
        raise Exception("This __getattribute__ always raises an exception: %r" % name)
        
class PicklingTest(TestCase):
    """
    This is the description of the class WfBaseTest. 
    
    Here comes the description of the class. The sole purpose
    of this class is to act as an example for documenting a class.
    """
    def run(self, result=None):
        self.dis = getattr(result, "showAll", False)
        self.dis = False
        return TestCase.run(self, result)
    
    def setUp(self):
        self.pickler = _sPickle.SPickleTools()
        
    def tearDown(self):
        pass

    def dumpWithPreobjects(self, preObjects, *obj, **kw):
        """Dump one or more objects.
        
        Returns the dump of the 2-tuple (preObjects, obj[0] if len(obj) == 1 else obj )
        Writes the opcodes to sys.stdout, if kw['dis'] evaluates to True.
        Writes the opcodes to sys.stderr, if an error occurs.
    
        """
        
        dis = kw.get("dis")
        try:
            toBeDumped = (preObjects, obj[0] if len(obj) == 1 else obj )
            p = self.pickler.dumps(toBeDumped, mangleModuleName=kw.get("mangleModuleName"))
            self.pickler.dis(p, out=StringIO())
        except:
            exinfo = sys.exc_info()
            l = []
            try:
                _sPickle.Pickler(l, 2).dump(toBeDumped)
            except:
                try:
                    l.append(pickle.STOP)
                    pickletools.dis("".join(l), out=sys.stderr)
                except:
                    traceback.print_exc(limit=1, file=sys.stderr)
            raise exinfo[0], exinfo[1], exinfo[2]
            
        if dis is None:
            dis = self.dis
        if dis:
            self.pickler.dis(p)
            print "len(pickle): ", len(p)
        return p
        
    # Test the object graph isomorphism for objects and their 
    # __dict__ attributes. (These tests fail with pickle.Pickler). 

    # using objects of a plain normal class
    def testObjDictConsistency(self):
        self.objDictConsistency(PlainClass, False, False)
    def testObjDictConsistencyHard(self):
        self.objDictConsistency(PlainClass, True)

    # using objects of a classic class
    def testObjDictConsistencyClassic(self):
        self.objDictConsistency(PlainClassicClass, False, False)
    def testObjDictConsistencyClassicHard(self):
        self.objDictConsistency(PlainClassicClass, True)

    # using objects of a class, that uses a
    # reducer, that uses a precreated __dict__ object
    def testObjDictConsistencyDCR(self):
        self.objDictConsistency(ClassWithDictCreatingReduce, False, False)
    def testObjDictConsistencyDCRHard(self):
        self.objDictConsistency(ClassWithDictCreatingReduce, True, False)

    def objDictConsistency(self, cls, hard, dis=False):
        """Is the relation between an object and its dictionary preserved"""
        orig = cls("OK")
        # the hard way 
        if hard:
            p = self.dumpWithPreobjects(None, orig.__dict__, orig, dis=dis)
            d, obj = self.pickler.loads(p)[-1]
        else:
            p = self.dumpWithPreobjects(None, orig, orig.__dict__, dis=dis)
            obj, d = self.pickler.loads(p)[-1]            
        self.assertTrue(type(obj)is type(orig))
        self.assertTrue(type(obj.__dict__) is type(orig.__dict__))
        self.assertEquals(set(obj.__dict__.keys()), set(orig.__dict__.keys()))
        self.assertTrue(obj.__dict__ is d)
        self.assertTrue(obj.isOk() is True)
    
    
    
    # Tests for modules
    #
    # - Contains the pickle the module content or just an import instruction?
    # - How is sys.modules changed on unpickling?
    # - Is the content of the modules equal?
 
#    # the function used to import a module   
#    def testImport(self):
#        orig = _sPickle.import_module
#        p = self.dumpWithPreobjects(None, orig, dis=False)
#        obj = self.pickler.loads(p)[-1]
#        self.assertIsNot(obj, orig)
#        self.assertDictEqual(orig.func_globals, obj.func_globals)
    
    def testModule(self):
        orig = tabnanny
        self.assertTrue(orig.__name__ in sys.modules)    
        obj, tif = self.moduleTest(orig)
        self.assertTrue(obj is tif.post_modules[orig.__name__])
    def testModuleU(self):
        orig = tabnanny
        self.assertTrue(orig.__name__ in sys.modules)
        obj, tif = self.moduleTest(orig, unimport=True, dis=False)
        self.assertTrue(obj is tif.post_modules[orig.__name__])
        self.assertTrue(orig.__name__ in tif.names, "name %r not in %r" % (orig.__name__, tif.names))
        
    def testWfModule(self):
        orig = wf_module
        self.assertTrue(orig.__name__ in sys.modules)
        obj, tif = self.wfModuleTest(orig)
        self.assertFalse(obj is tif.post_modules[orig.__name__])

    def testWfModuleU(self):
        orig = wf_module
        self.assertTrue(orig.__name__ in sys.modules)
        obj, tif = self.wfModuleTest(orig, unimport=True)
        self.assertEqual(obj, tif.post_modules.get(orig.__name__))
        self.assertTrue(obj is tif.post_modules[orig.__name__])

    def testStrangeWfModuleTest(self):
        orig = strangeModule
        self.assertTrue(orig.__name__ in sys.modules)
        obj, tif = self.wfModuleTest(orig)
        self.assertFalse(obj is tif.post_modules[orig.__name__])

    def testStrangeWfModuleTestU(self):
        orig = strangeModule
        self.assertTrue(orig.__name__ in sys.modules)
        obj, tif = self.wfModuleTest(orig, unimport=True)
        self.assertEqual(obj, tif.post_modules.get(orig.__name__))
        self.assertTrue(obj is tif.post_modules[orig.__name__])

    def testAnonymousModule(self):
        self.assertFalse(anonymousModule.__name__ in sys.modules)
        obj, tif = self.moduleTest(anonymousModule)
        self.assertFalse(obj.__name__ in tif.post_modules)
    def testAnonymousModule2(self):
        self.assertFalse(anonymousModule.__name__ in sys.modules)
        obj, tif = self.moduleTest(anonymousModule, anonymousModule.__dict__)
        self.assertFalse(obj.__name__ in tif.post_modules)

    def testAnonymousWfModule(self):
        self.assertFalse(anonymousWfModule.__name__ in sys.modules)
        obj, tif = self.wfModuleTest(anonymousWfModule)
        self.assertFalse(obj.__name__ in tif.post_modules)
    def testAnonymousWfModule2(self):
        self.assertFalse(anonymousWfModule.__name__ in sys.modules)
        obj, tif = self.wfModuleTest(anonymousWfModule, anonymousWfModule.__dict__)
        self.assertFalse(obj.__name__ in tif.post_modules)

    def testModuleWithWrongName(self):
        from . import mod_with_wrong_name
        orig = mod_with_wrong_name
        self.assertNotIn(orig.__name__, sys.modules)
        self.assertIn(orig.NAME, sys.modules)
        obj, tif = self.moduleTest(orig)
        self.assertNotIn(orig.__name__, tif.post_modules)
        self.assertIs(obj, tif.post_modules[orig.NAME])
    def testModuleWithWrongNameU(self):
        from . import mod_with_wrong_name
        orig = mod_with_wrong_name
        self.assertNotIn(orig.__name__, sys.modules)
        self.assertIn(orig.NAME, sys.modules)
        obj, tif = self.moduleTest(orig, unimport=True)
        self.assertNotIn(orig.__name__, tif.post_modules)
        self.assertIs(obj, tif.post_modules[orig.NAME])

    class MangleModuleName(object):
        """Add a prefix to certain module names"""
        def __init__(self, start, prefix, package=None):
            """
            Create a MangleModuleName
            
            :param start: mangle modules starting with *start*
            :param prefix: add *prefix* the name of a mangled module
            
            """
            self.start = start
            self.prefix = prefix
            self.package = package
        def __call__(self, pickler, name, module):
            """
            A sPickle.Pickler mangleModuleName functor
            """
            if module is os.path:
                return "os.path"
            if isinstance(name, str) and (name.startswith(self.start) or name == self.package):
                prefix = self.prefix
                class ReplacedModuleName(object):
                    def __reduce__(self):
                        return (operator.add, (prefix, name))               
                return ReplacedModuleName()
            return name
        def getMangledName(self, name, module=None):
            """Unit test helper function"""
            if module is os.path:
                return "os.path"
            if isinstance(name, str) and (name.startswith(self.start) or name == self.package):
                return self.prefix + name
            return name

    def testModule_MangleModuleName(self):
        orig = tabnanny
        self.assertTrue(orig.__name__ in sys.modules)
        mmn = self.MangleModuleName(orig.__name__, "renamed_", package=orig.__name__)
        mangledName = mmn.getMangledName(orig.__name__)

        replacedObj = types.ModuleType(mangledName)
        replacedObj.__dict__.update(orig.__dict__)
        replacedObj.__dict__['__name__'] = mangledName
        sys.modules[mangledName] = replacedObj
        try:
            obj, tif = self._moduleTestCommon(orig, mangleModuleName=mmn)
        finally:
            del sys.modules[mangledName]

        self.assertTrue(obj is tif.post_modules[mangledName])

    def testWfModule_MangleModuleName(self):
        orig = wf_module
        self.assertTrue(orig.__name__ in sys.modules)
        self.assertIsInstance(orig.__package__, str)
        package = orig.__name__.rpartition('.')[0]
        mmn = self.MangleModuleName(package + ".wf_mod", "renamed_", package=package)
        self.assertFalse(mmn.getMangledName(orig.__name__) in sys.modules)

        obj, tif = self.wfModuleTest(orig, mangleModuleName=mmn, dis=False)

        self.assertTrue(obj is tif.post_modules[mmn.getMangledName(orig.__name__)])
        self.assertIsInstance(obj.__package__, str)
        self.assertEqual(obj.__package__, mmn.getMangledName(orig.__package__))

    def testWfModuleU_MangleModuleName(self):
        orig = wf_module
        self.assertTrue(orig.__name__ in sys.modules)
        mmn = self.MangleModuleName(orig.__name__, "renamed_")
        self.assertFalse(mmn.getMangledName(orig.__name__) in sys.modules)
        obj, tif = self.wfModuleTest(orig, mangleModuleName=mmn, unimport=True)
        self.assertEqual(obj, tif.post_modules.get(mmn.getMangledName(orig.__name__)))
        self.assertTrue(obj is tif.post_modules[mmn.getMangledName(orig.__name__)])

    def testAnonymousWfModule_MangleModuleName(self):
        self.assertFalse(anonymousWfModule.__name__ in sys.modules)
        mmn = self.MangleModuleName(anonymousWfModule.__name__, "renamed_")
        obj, tif = self.wfModuleTest(anonymousWfModule, mangleModuleName=mmn, preObjects=anonymousWfModule.__name__, dis=False)
        self.assertFalse(obj.__name__ in tif.post_modules)

    def testAnonymousWfModule2_MangleModuleName(self):
        self.assertFalse(anonymousWfModule.__name__ in sys.modules)
        mmn = self.MangleModuleName(anonymousWfModule.__name__, "renamed_")
        obj, tif = self.wfModuleTest(anonymousWfModule, mangleModuleName=mmn, preObjects=anonymousWfModule.__dict__, dis=False)
        self.assertFalse(obj.__name__ in tif.post_modules)

    def testOsPath_MangleModuleName(self):
        orig = os.path
        self.assertNotEqual(orig.__name__, "os.path")
        mmn = self.MangleModuleName("DOES NOT APPLY", "", package=orig.__name__)
        p = self.dumpWithPreobjects(None, orig, dis=False, 
                                    mangleModuleName=mmn
                                    )

        il = self.pickler.getImportList(p)
        modules = set([i.partition(" ")[0] for i in il])
        self.assertNotIn(orig.__name__, modules)
        self.assertIn("os.path", modules)
        
        obj = self.pickler.loads(p)[1]
        self.assertIs(obj, orig)

    def testOsPathJoin_MangleModuleName(self):
        orig = os.path
        self.assertNotEqual(orig.__name__, "os.path")
        mmn = self.MangleModuleName("DOES NOT APPLY", "", package=orig.__name__)
        p = self.dumpWithPreobjects(None, orig.join, dis=False, 
                                    mangleModuleName=mmn
                                    )

        il = self.pickler.getImportList(p)
        modules = set([i.partition(" ")[0] for i in il])
        self.assertNotIn(orig.__name__, modules)
        self.assertIn("os.path", modules)
        
        obj = self.pickler.loads(p)[1]
        self.assertIs(obj, orig.join)

    @skipIf(gtk is None, "gtk not available")
    def testGtk(self):
        # the pure stackless pickler fails to import gtk
        self.moduleTest(gtk, dis=False)

    def moduleTest(self, module, preObjects=None, **kw):
        orig = module
        obj, tif = self._moduleTestCommon(orig, preObjects, **kw)
        if tif.pre_modules.has_key(orig.__name__):
            self.assertEquals(obj, orig)
            self.assertTrue(obj is orig)
            self.assertTrue(orig.__name__ in tif.names, "name %r not in %r" % (orig.__name__, tif.names))
        return (obj, tif)

    def wfModuleTest(self, wf_module, preObjects=None, **kw):
        orig = wf_module
        obj, tif = self._moduleTestCommon(orig, preObjects, **kw)
        # the object must be a real clone and must not be imported
        self.assertFalse(obj is orig)
        self.assertFalse(orig.__name__ in tif.names, "Import of forbidden module %r" % (orig.__name__,))
        return (obj, tif)
        
    def _moduleTestCommon(self, module, preObjects=None, unimport=(), dis=False, mangleModuleName=None):
        orig = module
        p = self.dumpWithPreobjects(preObjects, orig, dis=dis, mangleModuleName=mangleModuleName)
        
        if unimport:
            if unimport is True:
                unimport = (orig,)
        
        with TestImportFunctor(*unimport) as tif:
            # cPickle does not use our modified __import__ function for the GLOBAL op code
            obj = self.pickler.loads(p, useCPickle=False)[-1]

        # test obj        
        self.assertEquals(type(obj), type(orig))
        self.assertTrue(type(obj) is type(orig))
        oname = orig.__name__
        if mangleModuleName:
            oname = mangleModuleName.getMangledName(oname)
        self.assertEquals(obj.__name__, oname)
        self.assertEqual(set(obj.__dict__.iterkeys()), set(orig.__dict__.iterkeys()))
        if callable(getattr(orig, "isOk", None)):
            self.assertTrue(obj.isOk() is True)
        
        if tif.pre_modules.has_key(orig.__name__):
            # the import of the module must not change anything
            if mangleModuleName is None:
                self.assertEqual(tif.pre_modules, tif.post_modules)
            else:
                post = dict(tif.post_modules)
                for k in tif.pre_modules.keys():
                    mangled = mangleModuleName.getMangledName(k)
                    if mangled != k and k != mangleModuleName.package:
                        del post[mangled]
                self.assertEqual(tif.pre_modules, post)
            
        return (obj, tif)


    
    # Tests for module dictionaries
    #
    
    def testModuleDict(self):
        orig = tabnanny
        self.moduleDictTest(orig)        
    def testWfModuleDict(self):
        self.wfModuleDictTest(wf_module)
            

    def testAnonymousModuleDict(self):
        self.assertFalse(anonymousModule.__name__ in sys.modules)
        self.moduleDictTest(anonymousModule)
    def testAnonymousModuleDict2(self):
        self.assertFalse(anonymousModule.__name__ in sys.modules)
        self.moduleDictTest(anonymousModule, anonymousModule)
        

    def testAnonymousWfModuleDict(self):
        self.assertFalse(anonymousWfModule.__name__ in sys.modules)
        self.wfModuleDictTest(anonymousWfModule)
    def testAnonymousWfModuleDict2(self):
        self.assertFalse(anonymousWfModule.__name__ in sys.modules)
        self.wfModuleDictTest(anonymousWfModule, anonymousWfModule)
        
    def moduleDictTest(self, module, preObjects=None, dis=False):
        g = module.__dict__
        p = self.dumpWithPreobjects(preObjects, g, module)
        obj, mod = self.pickler.loads(p)[-1]
        self.assertTrue(obj is g)

    def wfModuleDictTest(self, wf_module, preObjects = None, dis=False):
        orig = getattr(wf_module, "__dict__")
        p = self.dumpWithPreobjects(preObjects, orig, wf_module)
        obj, mod = self.pickler.loads(p)[-1]
        self.assertTrue(type(obj) is type(orig))
        self.assertFalse(obj is orig)
        self.assertEqual(set(obj.keys()), set(orig.keys()))
        self.assertTrue(type(wf_module) is type(mod))
        self.assertTrue(mod.__dict__ is obj)

    # Tests for function and code objects
    
    def testWfFunction(self):
        self.wfFunctionTest(wf_module.isOk)
        self.assertTrue(wf_module is sys.modules[wf_module.__name__])
        
    def testStrangeWfModuleFunctionTest(self):
        f = strangeModule.isOk
        self.assertTrue(f())
        self.wfFunctionTest(f, strangeModule, dis=False)
        self.wfFunctionTest(f, dis=False)

    @skipIf(sys.hexversion < 0x02070000, "requires python 2.7")
    def testDecoratedFunctionTest(self):
        f = decoratedFunction
        self.assertTrue(f())
        obj = self.wfFunctionTest(f, dis=False)
        self.assertTrue(obj())
        
    def testPartiallyUnpickleable(self):
        f = partiallyUnpickleableFunction
        self.assertTrue(f())
        self.assertRaises(IntentionallyUnpicleableError, self.pickler.dumps, f)
            
    def wfFunctionTest(self, function, preObjects=None, dis=False):
        orig = function
        p = self.dumpWithPreobjects(preObjects, orig, dis=dis)
        obj = self.pickler.loads(p)[-1]
        self.assertTrue(type(obj) is type(orig))
        self.assertFalse(obj is orig)
        self.assertEqual(obj.__name__, orig.__name__)
        self.assertEqual(obj.__doc__, orig.__doc__)
        if hasattr(orig, "func_globals"):
            self.assertTrue(type(obj.func_globals) is type(orig.func_globals))
        if hasattr(orig, "func_code"):
            self.assertFalse(obj.func_code is orig.func_code)
            self.assertEquals(obj.func_code, orig.func_code)
        # Todo: compare the other attributes of a function
        return obj

    # Tests for function creation
    def testTypeCode(self):
        p = self.pickler.dumps(types.CodeType)
        obj = self.pickler.loads(p)
        self.assertTrue(obj is types.CodeType)

    def testTypeCell(self):
        cellType = type((lambda: self).func_closure[0])
        p = self.pickler.dumps(cellType)
        obj = self.pickler.loads(p)
        self.assertTrue(obj is cellType)
    
    def testCodeObject(self):
        # a function code object
        orig = self.testCodeObject.im_func.func_code
        self.codeObjectTest(orig, dis=False)

    def codeObjectTest(self, orig, dis=False):
        # a function code object
        self.assertIsInstance(orig, types.CodeType)
        p = self.dumpWithPreobjects(None, orig, dis=dis)
        obj = self.pickler.loads(p)[1]
        self.assertIsNot(obj, orig)
        self.assertIsInstance(obj, types.CodeType)
        self.assertEqual(obj.co_name, orig.co_name)
        self.assertEqual(obj.co_argcount, orig.co_argcount)
        self.assertEqual(obj.co_nlocals, orig.co_nlocals)
        self.assertEqual(obj.co_varnames, orig.co_varnames)
        self.assertEqual(obj.co_cellvars, orig.co_cellvars)
        self.assertEqual(obj.co_freevars, orig.co_freevars)
        self.assertEqual(obj.co_code, orig.co_code)
        self.assertEqual(obj.co_consts, orig.co_consts)
        self.assertEqual(obj.co_names, orig.co_names)
        self.assertEqual(obj.co_filename, orig.co_filename)
        self.assertEqual(obj.co_firstlineno, orig.co_firstlineno)
        self.assertEqual(obj.co_lnotab, orig.co_lnotab)
        self.assertEqual(obj.co_stacksize, orig.co_stacksize)
        self.assertEqual(obj.co_flags, orig.co_flags)
        

    # Tests for special objects and types
    
    def testDictSysModules(self):
        p = self.pickler.dumps(sys.modules)
        obj = self.pickler.loads(p)
        self.assertTrue(obj is sys.modules)

    def testDict__builtins__(self):
        from __builtin__ import __dict__ as bid
        self.assertIs(bid, __builtins__)
        p = self.dumpWithPreobjects(None, __builtins__, dis=False)
        obj = self.pickler.loads(p)[1]
        self.assertIs(obj, __builtins__)
        
    def testTypeType(self):
        p = self.pickler.dumps(type)
        obj = self.pickler.loads(p)
        self.assertTrue(obj is type)

    def testTypeClass(self):
        p = self.pickler.dumps(types.ClassType)
        obj = self.pickler.loads(p)
        self.assertTrue(obj is types.ClassType)

    def testThreadLock(self):
        lock = thread.allocate_lock()
        self.assertIsInstance(lock, thread.LockType)
        p = self.pickler.dumps(lock)
        obj = self.pickler.loads(p)
        self.assertIsInstance(obj, thread.LockType)
        self.assertIsNot(obj,lock)
        self.assertFalse(obj.locked())
        
        self.assertTrue(lock.acquire(0))
        p = self.pickler.dumps(lock)
        obj = self.pickler.loads(p)
        self.assertIsInstance(obj, thread.LockType)
        self.assertTrue(obj.locked())
        
    def testThreadLockAcquire(self):
        self.builtinMethodTest(thread.allocate_lock(), "acquire")
    def testThreadLockRelease(self):
        self.builtinMethodTest(thread.allocate_lock(), "release")
    def testThreadLockLocked(self):
        self.builtinMethodTest(thread.allocate_lock(), "locked")

    def builtinMethodTest(self, obj, methodName):
        method = getattr(obj, methodName)
        p = self.dumpWithPreobjects(method, obj, dis=False)
        restoredMethod, restoredObj = self.pickler.loads(p)
        self.assertIsInstance(restoredObj, thread.LockType)
        self.assertIsNot(restoredObj, obj)
        self.assertIs(restoredMethod.__self__, restoredObj)
        self.assertEqual(restoredMethod, getattr(restoredObj, methodName))
        
    def testObject__delattr__(self):
        orig = object.__delattr__
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testObject__format__(self):
        orig = object.__format__
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testObject__getattribute__(self):
        orig = object.__getattribute__
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testObject__hash__(self):
        orig = object.__hash__
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testObject__init__(self):
        orig = object.__init__
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testObject__new__(self):
        orig = object.__new__
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testObject__reduce__(self):
        orig = object.__reduce__
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testObject__reduce_ex__(self):
        orig = object.__reduce_ex__
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testObject__repr__(self):
        orig = object.__repr__
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testObject__setattr__(self):
        orig = object.__setattr__
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testObject__sizeof__(self):
        orig = object.__sizeof__
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testObject__str__(self):
        orig = object.__str__
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testObject__subclasshook__(self):
        orig = object.__subclasshook__
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertEqual(repr(obj), repr(orig))
        # There seems to be a python bug: 
        # [kruis@aragvi ~]$ python2.7 -c "print object.__subclasshook__ is object.__subclasshook__"
        # False
        #
        # self.assertIs(obj, orig)

    def testModuleNew(self):
        orig = types.ModuleType.__new__
        p = self.dumpWithPreobjects(None, orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)
        
        
    def testRpycBuiltinTypes(self):
        from rpyc.core.netref import _builtin_types
        errors = []
        for t in _builtin_types:
            try:
                p = self.dumpWithPreobjects(None, t, dis=False)
                obj = self.pickler.loads(p)[-1]
            except Exception:
                errors.append("Exception while pickling type %r: %s" % (t, traceback.format_exc()))
            else:
                if t is not obj:
                    errors.append("Expected type %r, got type %r" % (t, obj))
        self.assertFalse(errors, os.linesep.join(errors))
        
    def testTypeStaticmethod(self):
        orig = staticmethod
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testStaticmethod(self):
        orig = staticmethod(aFunction)
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(type(obj), type(orig))
        self.assertIsNot(obj, orig)
        self.assertIs(obj.__func__, orig.__func__)

    def testTypeClassmethod(self):
        orig = classmethod
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)
        
    def testClassmethod(self):
        orig = classmethod(aFunction)
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(type(obj), type(orig))
        self.assertIsNot(obj, orig)
        self.assertIs(obj.__func__, orig.__func__)
        
    def testTypeProperty(self):
        orig = property
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)
        
    def testProperty(self):
        doc = "docstring"        
        orig = property(aFunction, aFunction2, aFunction3, doc)
        self.assertIs(orig.fget, aFunction)
        self.assertIs(orig.fset, aFunction2)
        self.assertIs(orig.fdel, aFunction3)
        self.assertEqual(orig.__doc__, doc)

        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(type(obj), type(orig))
        self.assertIsNot(obj, orig)
        self.assertIs(obj.fget, aFunction)
        self.assertIs(obj.fset, aFunction2)
        self.assertIs(obj.fdel, aFunction3)
        self.assertEqual(obj.__doc__, doc)
        
    def testTypeOperatorItemgetter(self):
        orig = operator.itemgetter
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testOperatorItemgetter(self):
        orig = operator.itemgetter(1,3,5)
        self.assertTupleEqual(('B', 'D', 'F'), orig("ABCDEFG"))
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(type(obj), type(orig))
        self.assertIsNot(obj, orig)
        self.assertTupleEqual(('B', 'D', 'F'), obj("ABCDEFG"))

    def testTypeOperatorAttrgetter(self):
        orig = operator.attrgetter
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testOperatorAttrgetter(self):
        target = lambda : None
        target.a = 1
        target.b = lambda : None
        target.b.c = 2
        target.d = lambda : None
        target.d.e = lambda : None
        target.d.e.f = 3
        target.x = 99
        target.y = 98
        target.b.x= 97
        
        orig = operator.attrgetter("a","b.c","d.e.f")
        
        self.assertTupleEqual((1, 2, 3), orig(target))
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(type(obj), type(orig))
        self.assertIsNot(obj, orig)
        self.assertTupleEqual((1, 2, 3), obj(target))

    def testXrange(self):
        orig = xrange(4,10,2)
        p = self.dumpWithPreobjects(None,orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIsInstance(obj, xrange)
        self.assertListEqual(list(obj), list(orig))

    def testRangeIterator(self):
        x = xrange(2,10,2)
        orig = iter(x)
        lorig = []
        lorig.append(orig.next())
        lorig.append(orig.next())
        lobj = lorig[:]
        p = self.dumpWithPreobjects(None,orig, dis=False)
        lorig.extend(orig)
        self.assertListEqual(lorig, list(x))
        obj = self.pickler.loads(p)[-1]
        self.assertIsInstance(obj, type(orig))
        lobj.extend(obj)
        self.assertListEqual(lobj, lorig)
        
    def testDictProxy(self):
        dp = PlainClass.__dict__
        self.assertIsInstance(dp, types.DictProxyType)
        p = self.dumpWithPreobjects(None, dp)
        obj = self.pickler.loads(p)[-1]
        self.assertIsInstance(obj, types.DictProxyType)
        self.assertEqual(dp, obj)

    def testDictProxyNotClassDict(self):
        class C(object):
            __slots__ = ()
        dp = C.__dict__
        self.assertIsInstance(dp, types.DictProxyType)
        self.assertRaises(pickle.PicklingError, self.pickler.dumps, dp)
        
    def testMemberDescriptor(self):
        class C(object):
            __slots__ = ('a')
        orig = C.__dict__['a']
        self.assertIsInstance(orig, types.MemberDescriptorType)
        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIsInstance(obj, types.MemberDescriptorType)
        self.assertEqual(orig.__name__, obj.__name__)
        self.assertClassEquals(orig.__objclass__, obj.__objclass__)
        
    def testGetSetDescriptor(self):
        orig = PlainClass.__weakref__
        self.assertIsInstance(orig, types.GetSetDescriptorType)
        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIsInstance(obj, types.GetSetDescriptorType)
        self.assertEqual(orig.__name__, obj.__name__)
        self.assertClassEquals(orig.__objclass__, obj.__objclass__)

    def testCStringIoInputType(self):
        orig = cStringIO.InputType
        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testCStringIoOutputType(self):
        orig = cStringIO.OutputType
        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)
                
    def testCStringIoOutput(self):
        orig = cStringIO.StringIO()

        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIsNot(obj, orig)
        self.assertIsInstance(obj, type(orig))
        self.assertEqual(obj.getvalue(), orig.getvalue())
        self.assertEqual(obj.tell(), orig.tell())
        
        orig.write("0123456789")
        
        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIsNot(obj, orig)
        self.assertIsInstance(obj, type(orig))
        self.assertEqual(obj.getvalue(), orig.getvalue())
        self.assertEqual(obj.tell(), orig.tell())

        orig.seek(5,0)

        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIsNot(obj, orig)
        self.assertIsInstance(obj, type(orig))
        self.assertEqual(obj.getvalue(), orig.getvalue())
        self.assertEqual(obj.tell(), orig.tell())
        
        orig.close()
        self.assertRaises(ValueError, orig.getvalue)
        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIsNot(obj, orig)
        self.assertIsInstance(obj, type(orig))
        self.assertRaises(ValueError, obj.getvalue)
        
    def testCStringIoInput(self):
        orig = cStringIO.StringIO("")

        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIsNot(obj, orig)
        self.assertIsInstance(obj, type(orig))
        self.assertEqual(obj.getvalue(), orig.getvalue())
        self.assertEqual(obj.tell(), orig.tell())
        
        orig = cStringIO.StringIO("0123456789")
        
        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIsNot(obj, orig)
        self.assertIsInstance(obj, type(orig))
        self.assertEqual(obj.getvalue(), orig.getvalue())
        self.assertEqual(obj.tell(), orig.tell())

        orig.seek(5,0)

        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIsNot(obj, orig)
        self.assertIsInstance(obj, type(orig))
        self.assertEqual(obj.getvalue(), orig.getvalue())
        self.assertEqual(obj.tell(), orig.tell())
        
        orig.close()
        self.assertRaises(ValueError, orig.getvalue)
        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIsNot(obj, orig)
        self.assertIsInstance(obj, type(orig))
        self.assertRaises(ValueError, obj.getvalue)

    def testOrderedDict(self):
        # the method __reduce__ of collections.OrderedDict fails for recursive
        # dictionaries. Test our fix
        orig = collections.OrderedDict([['key1', 'value1'], ['key2', 'value2']])
        orig['self'] = orig # a recursive dict. 
        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIsNot(obj, orig)
        self.assertIsInstance(obj, collections.OrderedDict)
        self.assertIs(obj, obj['self'])
        # assertEqual fails to handle a recursive object
        del obj['self']
        del orig['self']
        self.assertEqual(obj, orig)

    def testTypeWeakrefReferenceType(self):
        orig = weakref.ReferenceType
        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testTypeWeakrefProxyType(self):
        orig = weakref.ProxyType
        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testTypeWeakrefCallableProxyType(self):
        orig = weakref.CallableProxyType
        p = self.dumpWithPreobjects(None, orig)
        obj = self.pickler.loads(p)[-1]
        self.assertIs(obj, orig)

    def testWeakrefRef1(self):
        something = PlainClass(123)
        orig = weakref.ref(something)
        p = self.dumpWithPreobjects(something, orig, dis=False)
        obj, obj2 = self.pickler.loads(p)
        self.assertIsNot(obj, something)
        self.assertIsInstance(obj, type(something))
        self.assertIsNot(obj2, orig)
        self.assertIsInstance(obj2, type(orig))
        self.assertIs(obj2(), obj)

    def testWeakrefRef2(self):
        # dead ref case
        orig = weakref.ref(PlainClass(123))
        p = self.dumpWithPreobjects(None, orig, dis=False)
        obj = self.pickler.loads(p)[-1]
        self.assertIsNot(obj, orig)
        self.assertIsInstance(obj, type(orig))
        self.assertIsNone(obj())

    # Tests for pickling classes
    
    def testClassicClass(self):
        class ClassicClass:
            anAttribute = None
        self.classCopyTest(ClassicClass)
    
    def testNewStyleClass(self):
        class NewStyleClass(object):
            anAttribute = None
        self.classCopyTest(NewStyleClass)

    def testNewStyleClassWithSlots(self):
        class NewStyleClassWithSlots(object):
            __slots__ = ("slot1", "slot2")
            anAttribute = None
        self.classCopyTest(NewStyleClassWithSlots)

    def testNewStyleClassWithSlotsAndDict(self):
        class NewStyleClassWithSlotsAndDict(object):
            __slots__ = ("slot1", "slot2", "__dict__")
            anAttribute = None
        self.classCopyTest(NewStyleClassWithSlotsAndDict)

    def testRecursiveClass(self):
        cls = self.classCopyTest(P.RecursiveClass)
        cls().run(self)
        
    def testIndirectRecursiveClass(self):
        cls = self.classCopyTest(Q.IndirectRecursiveClass)
        cls().run(self)

    def testIndirectRecursiveClass2(self):
        cls = self.classCopyTest(R.IndirectRecursiveClass)
        cls().run(self)
        
    def testNormalClass(self):
        cls = self.classCopyTest(pickle.Pickler)
        self.assertTrue(cls is pickle.Pickler)
        
    def testNamedTuple(self):
        cls = self.classCopyTest(collections.namedtuple("namedTupleName", "a b"), dis=False)
        nt = cls(1, 2)
        self.assertTupleEqual(nt, (1,2))
        self.assertEqual(nt.a, 1)
        self.assertEqual(nt.b, 2)
        
    def testExceptionClass(self):
        class TestException(BaseException):
            attribute = "attribute value"
        self.classCopyTest(TestException, dis=False)
        
    def testClassWithForainMemberDescriptor(self):
        class C(object):
            __slots__ = ('md')
        md = C.__dict__['md']
        self.assertIsInstance(md, types.MemberDescriptorType)
        
        class orig:
            pass
        orig.md = md
            
        cls = self.classCopyTest(orig, dis=False)
        self.assertIsInstance(cls.md, types.MemberDescriptorType)

    def assertClassEquals(self, origCls, cls, level=0):
        if origCls is cls:
            return
        self.assertLess(level, 10, "Possible recursion detected")
        self.assertTrue(type(cls) is type(origCls))
        self.assertEqual(cls.__name__, origCls.__name__)
        self.assertEqual(cls.__module__, origCls.__module__)
        self.assertEqual(len(cls.__bases__), len(origCls.__bases__))
        for b1, b2 in zip(cls.__bases__, origCls.__bases__):
            self.assertClassEquals(b1, b2, level+1)
        self.assertEqual(set(dir(cls)), set(dir(origCls)))
        for k in dir(cls):
            if k in ('__dict__', '__subclasshook__', '__weakref__'):
                continue
            v1 = getattr(cls, k)
            v2 = getattr(origCls, k)
            self.assertEqual(type(v1), type(v2), "key: %r type: %r != %r" % (k, v1, v2))
            if " at 0x" not in repr(v2):
                self.assertEqual(repr(v1), repr(v2), "key: %r repr: %r != %r" % (k, v1, v2))
            self.assertEqual(getattr(v1,"__name__", "Object has no Name"), getattr(v2,"__name__", "Object has no Name"))

    def classCopyTest(self, origCls, dis=False):
        p = self.dumpWithPreobjects(None,origCls, dis=dis)
        cls = self.pickler.loads(p)[-1]
        self.assertClassEquals(origCls, cls)
        return cls
        
    # Import tests
        
    def testWfImports(self):
        orig = wf_module
        p = self.pickler.dumps(orig)
        importList = self.pickler.getImportList(p)
        self.assertIsInstance(importList, list)
        for module in importList:
            self.assertIsInstance(module, str)
            self.assertEqual(len(module.split(" ")), 2)

    # Handling of resource objects (files, socket, socketpair)

    def testResources(self):
        openFile = open(os.devnull)
        closedFile = open(os.devnull, "wb")
        closedFile.close()
        socket_ = socket.socket()
        if hasattr(socket, "socketpair"):
            sp = socket.socketpair()
            sp[1].close()
        else:
            sp = [socket.socket()]
            sp.append(sp[0]._sock)
            
        try:
            orig = (openFile, closedFile, sys.__stdout__, socket_, sp )
            logging.disable(logging.WARNING)
            try:
                p = self.pickler.dumps(orig)
            finally:
                logging.disable(0)
            restored = self.pickler.loads(p)
            
            self.assertIsInstance(restored, tuple)
            self.assertEqual(len(orig), len(restored))
            for i in range(len(orig)-1):
                self.assertIs(type(orig[i]), type(restored[i]))
            self.assertIsInstance(restored[-1][1], _sPickle.SOCKET_PAIR_TYPE)
            self.assertIs(orig[2], restored[2])
            
            self.assertEqual(orig[0].mode, restored[0].mode)
            self.assertFalse(restored[0].closed)
            restored[0].close()
            self.assertEqual(orig[1].mode, restored[1].mode)
            self.assertTrue(restored[1].closed)
        finally:
            openFile.close()
            socket_.close()
            sp[0].close()
        
        
    # Test hostile objects
    def testClassWithHostile__getattr__(self):
        self.classCopyTest(ClassWithHostile__getattr__, False)

    def testObjectWithHostile__getattr__1(self):
        orig = ClassWithHostile__getattr__(False)
        self.assertRaises(_sPickle.UnpicklingWillFailError, self.pickler.dumps, orig)

    def testObjectWithHostile__getattr__2(self):
        orig = ClassWithHostile__getattr__(True)
        p = self.dumpWithPreobjects(None, orig, dis=False)
        # import pickle as pickle_ ; pickler.cPickle =  pickle_
        restored = self.pickler.loads(p)[-1]
        self.assertIsNot(restored, orig)
        self.assertIsInstance(restored, orig.__class__)
    def testClassWithHostile__getattribute__(self):
        self.classCopyTest(ClassWithHostile__getattribute__, False)

    def testObjectWithHostile__getattribute__1(self):
        orig = ClassWithHostile__getattribute__(False)
        self.assertRaises(_sPickle.UnpicklingWillFailError, self.pickler.dumps, orig)

    def testObjectWithHostile__getattribute__2(self):
        orig = ClassWithHostile__getattribute__(True)
        p = self.dumpWithPreobjects(None, orig, dis=False)
        # import pickle as pickle_ ; pickler.cPickle =  pickle_
        restored = self.pickler.loads(p)[-1]
        self.assertIsNot(restored, orig)
        self.assertIsInstance(restored, orig.__class__)
        
class SPickleToolsTest(TestCase):
    def testModule_for_globals(self):
        pt = _sPickle.SPickleTools()
        self.assertIs(pt.module_for_globals({}), None)
        self.assertIs(pt.module_for_globals(_sPickle.__dict__), _sPickle)
        self.assertIs(pt.module_for_globals(pt.module_for_globals), _sPickle)
        self.assertIs(pt.module_for_globals(pt.module_for_globals, withDefiningModules=True), _sPickle)

    def testReducer(self):
        rvOrig = (1,2,3,4,5)
        reducer = _sPickle.SPickleTools.reducer(*rvOrig)
        self.assertIsInstance(reducer, object)
        rv = reducer.__reduce__()
        self.assertTupleEqual(rv, rvOrig)

        pickler = _sPickle.SPickleTools()
        p = pickler.dumps(_sPickle.SPickleTools.reducer(operator.add, (1,2)))
        obj = pickler.loads(p)
        self.assertIsInstance(obj, int)
        self.assertEqual(obj, 3)

if __name__ == "__main__":
    import unittest
    unittest.main()
