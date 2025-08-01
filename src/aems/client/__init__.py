from types import MethodType


class dualmethod:
    """Descriptor to allow class and instance methods of the same name.

    This class implements a descriptor that works similar to the
    classmethod() built-ins and can be used as a decorator, like the
    property() built-in. Instead of a method being only a class or
    instance method, two methods can share the same name and be accessed
    as an instance method or a class method based on the context.

    Example:

    >>> class Foo:
    ...     @dualmethod
    ...     def bar(self):
    ...         print('instance method for', self)
    ...     @bar.classmethod
    ...     def bar(cls):
    ...         print('class method for', cls)
    ...
    >>> Foo.bar()
    class method for <class '__main__.Foo'>
    >>> Foo().bar()
    instance method for <__main__.Foo object at 0x7fcd744f6610>
    >>>
    """

    def __init__(self, finstance=None, fclass=None, doc=None):
        """Instantiate the descriptor with the given parameters.

        If finstance is set, it must be a method implementing instance
        access. If fclass is set, it must be a method implementing class
        access similar to a classmethod. If doc is set, it will be used
        for the __doc__ attribute.  Otherwise, the __doc__ attribute
        from the instance or class method will be used, in that order.
        """
        self.finstance = finstance
        self.fclass = fclass
        if doc is not None:
            self.__doc__ = doc
        elif finstance is not None:
            self.__doc__ = finstance.__doc__
        elif fclass is not None:
            self.__doc__ = fclass.__doc__

    def __get__(self, instance, owner):
        """Descriptor getter method.

        See Python descriptor documentation."""
        if instance is None:
            if self.fclass is None:
                if self.finstance is None:
                    raise AttributeError("no instance or class method is set")
                return MethodType(self.finstance, instance)
            return MethodType(self.fclass, owner)
        if self.finstance is None:
            if self.fclass is None:
                raise AttributeError("no instance or class method is set")
            return MethodType(self.fclass, owner)
        return MethodType(self.finstance, instance)

    def instancemethod(self, finstance):
        """Descriptor to set the instance method."""
        self.finstance = finstance
        return self

    def classmethod(self, fclass):
        """Descriptor to set the class method."""
        self.fclass = fclass
        return self
