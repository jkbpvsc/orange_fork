import re
import logging
import subprocess
from os import path
from itertools import chain, repeat
from functools import lru_cache
from collections import OrderedDict

import bottlechest as bn
import numpy as np

from chardet.universaldetector import UniversalDetector

from Orange.data.variable import *
from Orange.util import abstract, Registry, flatten, namegen


log = logging.getLogger()

_IDENTITY = lambda i: i


class Compression:
    GZIP = '.gz'
    BZIP2 = '.bz2'
    XZ = '.xz'
    all = (GZIP, BZIP2, XZ)


def open_compressed(filename, *args, _open=open, **kwargs):
    """Return seamlessly decompressed open file handle for `filename`"""
    if isinstance(filename, str):
        if filename.endswith(Compression.GZIP):
            from gzip import open as _open
        elif filename.endswith(Compression.BZIP2):
            from bz2 import open as _open
        elif filename.endswith(Compression.XZ):
            from lzma import open as _open
        return _open(filename, *args, **kwargs)
    # Else already a file, just pass it through
    return filename


def detect_encoding(filename):
    """
    Detect encoding of `filename`, which can be a ``str`` filename, a
    ``file``-like object, or ``bytes``.
    """
    # Try with Unix file utility first because it's faster (~10ms vs 100ms)
    if isinstance(filename, str) and not filename.endswith(Compression.all):
        try:
            with subprocess.Popen(('file', '--brief', '--mime-encoding', filename),
                                  stdout=subprocess.PIPE) as process:
                process.wait()
                if process.returncode == 0:
                    encoding = process.stdout.read().strip()
                    # file only supports these encodings; for others it says
                    # unknown-8bit or binary. So we give chardet a chance to do
                    # better
                    if encoding in (b'utf-8', b'us-ascii', b'iso-8859-1',
                                    b'utf-7', b'utf-16le', b'utf-16be', b'ebcdic'):
                        return encoding.decode('us-ascii')
        except OSError: pass  # windoze

    # file not available or unable to guess the encoding, have chardet do it
    detector = UniversalDetector()

    def _from_file(f):
        for line in f:
            detector.feed(line)
            if detector.done: break
        detector.close()
        return detector.result.get('encoding')

    if isinstance(filename, str):
        with open_compressed(filename, 'rb') as f:
            return _from_file(f)
    elif isinstance(filename, bytes):
        detector.feed(filename)
        detector.close()
        return detector.result.get('encoding')
    elif hasattr(filename, 'encoding'):
        return filename.encoding
    else:  # assume file-like object that you can iter through
        return _from_file(filename)


class Flags:
    """Parser for column flags (i.e. third header row)"""
    DELIMITER = ' '
    _RE_SPLIT = re.compile(r'(?<!\\)' + DELIMITER)
    ALL = OrderedDict((
        ('class',     'c'),
        ('ignore',    'i'),
        ('meta',      'm'),
        ('weight',    'w'),
        ('.+?=.*?',    ''),  # general key=value attributes
    ))
    _RE_ALL = re.compile(r'^({})$'.format('|'.join(filter(None, flatten(ALL.items())))))

    def __init__(self, flags):
        for v in filter(None, self.ALL.values()):
            setattr(self, v, False)
        self.attributes = {}
        for flag in flags or []:
            flag = flag.strip()
            if self._RE_ALL.match(flag):
                if '=' in flag:
                    k, v = flag.split('=', 1)
                    self.attributes[k] = v
                else:
                    setattr(self, flag, True)
                    setattr(self, self.ALL.get(flag, ''), True)
            elif flag:
                log.warning('Invalid attribute flag \'{}\''.format(flag))

    @staticmethod
    def join(iterable, *args):
        return Flags.DELIMITER.join(i.strip().replace(Flags.DELIMITER, '\\' + Flags.DELIMITER)
                                    for i in chain(iterable, args)).lstrip()

    @staticmethod
    def split(s):
        return [i.replace('\\' + Flags.DELIMITER, Flags.DELIMITER)
                for i in Flags._RE_SPLIT.split(s)]


# Matches discrete specification where all the values are listed, space-separated
_RE_DISCRETE_LIST = re.compile(r'^\s*[^\s]+(\s[^\s]+)+\s*$')
_RE_TYPES = re.compile(r'^\s*({}|{}|)\s*$'.format(_RE_DISCRETE_LIST.pattern,
                                                  '|'.join(flatten(getattr(vartype, 'TYPE_HEADERS')
                                                                   for vartype in Variable.registry.values()))))
_RE_FLAGS = re.compile(r'^\s*( |{})*\s*$'.format('|'.join(flatten(filter(None, i) for i in Flags.ALL.items()))))


class FileFormatMeta(Registry):

    def __new__(cls, name, bases, attrs):
        newcls = super().__new__(cls, name, bases, attrs)

        # Optionally add compressed versions of extensions as supported
        if getattr(newcls, 'SUPPORT_COMPRESSED', False):
            new_extensions = list(getattr(newcls, 'EXTENSIONS', ()))
            for compression in Compression.all:
                for ext in newcls.EXTENSIONS:
                    new_extensions.append(ext + compression)
            newcls.EXTENSIONS = tuple(new_extensions)

        return newcls

    @property
    def formats(self):
        return self.registry.values()

    @lru_cache(5)
    def _ext_to_attr_if_attr2(self, attr, attr2):
        """
        Return ``{ext: `attr`, ...}`` dict if ``cls`` has `attr2`.
        If `attr` is '', return ``{ext: cls, ...}`` instead.
        """
        return OrderedDict((ext, getattr(cls, attr, cls))
                            for cls in self.registry.values()
                            if hasattr(cls, attr2)
                            for ext in getattr(cls, 'EXTENSIONS', []))

    @property
    def names(self):
        return self._ext_to_attr_if_attr2('DESCRIPTION', '__class__')

    @property
    def writers(self):
        return self._ext_to_attr_if_attr2('', 'write_file')

    @property
    def readers(self):
        return self._ext_to_attr_if_attr2('', 'read_file')

    @property
    def img_writers(self):
        return self._ext_to_attr_if_attr2('', 'write_image')

    @property
    def graph_writers(self):
        return self._ext_to_attr_if_attr2('', 'write_graph')


@abstract
class FileFormat(metaclass=FileFormatMeta):
    """
    Subclasses set the following attributes and override the following methods:

        EXTENSIONS = ('.ext1', '.ext2', ...)
        DESCRIPTION = 'human-readable file format description'
        SUPPORT_COMPRESSED = False

        @classmethod
        def read_file(cls, filename, wrapper=_IDENTITY):
            ...  # load headers, data, ...
            return wrapper(self.data_table(data, headers))

        @classmethod
        def write_file(cls, filename, data):
            ...
            self.write_headers(writer.write, data)
            writer.writerows(data)

    Wrapper FileFormat.data_table() returns Orange.data.Table from `data`
    iterable (list (rows) of lists of values (cols)). `wrapper` is the
    desired output class (if other than Table).
    """
    @staticmethod
    def open(filename, *args, **kwargs):
        """
        Format handlers can use this method instead of the builtin ``open()``
        to transparently (de)compress files if requested (according to
        `filename` extension). Set ``SUPPORT_COMPRESSED=True`` if you use this.
        """
        return open_compressed(filename, *args, **kwargs)

    @classmethod
    def read(cls, filename, wrapper=None):
        for ext, reader in cls.readers.items():
            if filename.endswith(ext):
                return reader.read_file(filename, wrapper)
        else: raise IOError('No readers for file "{}"'.format(filename))

    @classmethod
    def write(cls, filename, data):
        for ext, writer in cls.writers.items():
            if filename.endswith(ext):
                return writer.write_file(filename, data)
        else: raise IOError('No writers for file "{}"'.format(filename))

    @staticmethod
    def parse_headers(data):
        """Return (header rows, rest of data) as discerned from `data`"""
        all_digits = lambda i: str(i).replace('.', '').replace(',', '').isdigit()
        HEADER_TESTS = [
            # First line is not a header if more than a fraction of values consist of digits only
            lambda items: sum(all_digits(i) for i in items) / len(items) < .1,
            # Second row items are type identifiers
            lambda items: all(map(_RE_TYPES.match, items)),
            # Third row items are flags and column attributes (attr=value)
            lambda items: all(map(_RE_FLAGS.match, items)),
        ]
        header_rows = []
        first_nonheader_row = []
        for test in HEADER_TESTS:
            try: row = next(data)
            except StopIteration: break
            if not isinstance(row, (list, tuple)):
                row = list(row)
            if not test(row):
                first_nonheader_row = [row]
                break
            header_rows.append([i.strip() for i in row])
        return header_rows, chain(first_nonheader_row, data)

    @classmethod
    def data_table(self, data, headers=None):
        """
        Return Orange.data.Table given rows of `headers` (iterable of iterable)
        and rows of `data` (iterable of iterable; if ``numpy.ndarray``, might
        as well **have it sorted column-major**, e.g. ``order='F'``).

        Basically, the idea of subclasses is to produce those two iterables,
        however they might.

        If `headers` is not provided, the header rows are extracted from `data`,
        assuming they precede it.
        """
        if not headers:
            headers, data = self.parse_headers(data)

        # Consider various header types (single-row, two-row, three-row, none)
        if 3 == len(headers):
            names, types, flags = map(list, headers)
        else:
            if 1 == len(headers):
                HEADER1_FLAG_SEP = '#'
                # First row format either:
                #   1) delimited column names
                #   2) -||- with type and flags prepended, separated by #,
                #      e.g. d#sex,c#age,cC#IQ
                _flags, names = zip(*[i.split(HEADER1_FLAG_SEP, 1) if HEADER1_FLAG_SEP in i else ('', i)
                                      for i in headers[0]])
                names = list(names)
            elif 2 == len(headers):
                names, _flags = map(list, headers)
            else:
                # Use heuristics for everything
                names, _flags = [], []
            types = [''.join(filter(str.isupper, flag)).lower() for flag in _flags]
            flags = [Flags.join(filter(str.islower, flag)) for flag in _flags]

        # Determine maximum row length
        rowlen = max(map(len, (names, types, flags)))

        def _equal_length(lst):
            lst.extend(['']*(rowlen - len(lst)))
            return lst

        # Ensure all data is of equal width in a column-contiguous array
        data = np.array([_equal_length(list(row)) for row in data if any(row)],
                        copy=False, dtype=object, order='F')

        # Data may actually be longer than headers were
        try: rowlen = data.shape[1]
        except IndexError: pass
        else:
            for lst in (names, types, flags):
                _equal_length(lst)

        NAMEGEN = namegen('Feature ', 1)
        Xcols, attrs = [], []
        Mcols, metas = [], []
        Ycols, clses = [], []
        Wcols = []

        # Iterate through the columns
        for col in range(rowlen):
            flag = Flags(Flags.split(flags[col]))
            if flag.i: continue

            try:
                orig_values = [np.nan if i.strip() in MISSING_VALUES else i.strip()
                               for i in data[:, col]]
            except IndexError:
                # No data instances leads here
                orig_values = []
                # In this case, coltype could be anything. It's set as-is
                # only to satisfy test_table.TableTestCase.test_append
                coltype = DiscreteVariable

            type_flag = types and types[col].strip()
            coltype_kwargs = {}
            valuemap = []
            values = orig_values

            if type_flag in StringVariable.TYPE_HEADERS:
                coltype = StringVariable

            elif type_flag in ContinuousVariable.TYPE_HEADERS:
                coltype = ContinuousVariable
                values = [float(i) for i in orig_values]

            elif (type_flag in DiscreteVariable.TYPE_HEADERS or
                  _RE_DISCRETE_LIST.match(type_flag)):
                if _RE_DISCRETE_LIST.match(type_flag):
                    valuemap = Flags.split(type_flag)
                    coltype_kwargs.update(ordered=True)
                else:
                    valuemap = sorted(set(orig_values) - {np.nan})

            else:
                # No known type specified, use heuristics
                is_discrete = is_discrete_values(orig_values)
                if is_discrete:
                    valuemap = sorted(is_discrete)
                else:
                    try: values = [float(i) for i in orig_values]
                    except ValueError:
                        coltype = StringVariable
                    else:
                        coltype = ContinuousVariable

            if valuemap:
                # Map discrete data to ints
                def valuemap_index(val):
                    try: return valuemap.index(val)
                    except ValueError: return np.nan

                values = np.vectorize(valuemap_index, otypes=[float])(orig_values)
                coltype = DiscreteVariable
                coltype_kwargs.update(values=valuemap)

            # Write back the changed data. This is needeed to pass the
            # correct, converted values into Table.from_numpy below
            try: data[:, col] = values
            except IndexError: pass

            if flag.m or coltype is StringVariable:
                append_to = (Mcols, metas)
            elif flag.w:
                append_to = (Wcols, None)
            elif flag.c:
                append_to = (Ycols, clses)
            else:
                append_to = (Xcols, attrs)

            cols, domain_vars = append_to
            cols.append(col)
            if domain_vars is not None:
                var = coltype.make(names[col] if names and names[col] else next(NAMEGEN), **coltype_kwargs)
                var.attributes.update(flag.attributes)
                domain_vars.append(var)

                # Reorder discrete values to match existing variable
                if var.is_discrete and not var.ordered:
                    new_order, old_order = var.values, coltype_kwargs.get('values', var.values)
                    if new_order != old_order:
                        offset = len(new_order)
                        column = data[:, col] if data.ndim > 1 else data
                        column += offset
                        for i, val in enumerate(var.values):
                            try: oldval = old_order.index(val)
                            except ValueError: continue
                            bn.replace(column, offset + oldval, new_order.index(val))

        # If single-header or no-header mode and no class variable marked,
        # use the last attribute as class var
        if len(headers) <= 1 and not clses and len(attrs) > 1:
            clses.append(attrs.pop())
            Ycols.append(Xcols.pop())

        from Orange.data import Table, Domain
        domain = Domain(attrs, clses, metas)

        if not data.size:
            return Table.from_domain(domain, 0)

        table = Table.from_numpy(domain,
                                 data[:, Xcols].astype(float, order='C'),
                                 data[:, Ycols].astype(float, order='C'),
                                 data[:, Mcols].astype(object, order='C'),
                                 data[:, Wcols].astype(float, order='C'))
        return table

    @staticmethod
    def header_names(data):
        return [v.name for v in chain(namegen('weights_', data.W.shape[1], count=range),
                                      data.domain.attributes,
                                      data.domain.class_vars,
                                      data.domain.metas)]

    @staticmethod
    def header_types(data):
        def _vartype(var):
            if var.is_continuous or var.is_string:
                return var.TYPE_HEADERS[0]
            elif var.is_discrete:
                return Flags.join(var.values) if var.ordered else var.TYPE_HEADERS[0]
            raise NotImplementedError
        return list(chain(repeat('continuous', data.W.shape[1]),
                          (_vartype(v)
                           for v in chain(data.domain.attributes,
                                          data.domain.class_vars,
                                          data.domain.metas))))

    @staticmethod
    def header_flags(data):
        return list(chain(repeat('weight', data.W.shape[1]),
                          (Flags.join([flag], *('{}={}'.format(*a)
                                                for a in sorted(var.attributes.items())))
                           for flag, var in chain(zip(repeat(''),  data.domain.attributes),
                                                  zip(repeat('class'), data.domain.class_vars),
                                                  zip(repeat('meta'), data.domain.metas)))))

    @classmethod
    def write_headers(cls, write, data):
        """`write` is a callback that accepts an iterable"""
        write(cls.header_names(data))
        write(cls.header_types(data))
        write(cls.header_flags(data))

    @classmethod
    def write(cls, filename, data):
        return cls.write_file(filename, data)


class CSVFormat(FileFormat):
    EXTENSIONS = ('.csv',)
    DESCRIPTION = 'Comma-separated values'
    DELIMITER = ','
    SUPPORT_COMPRESSED = True

    @classmethod
    def read_file(cls, filename, wrapper=None):
        wrapper = wrapper or _IDENTITY
        encoding = detect_encoding(filename)
        import csv
        with cls.open(filename, mode='rt', newline='', encoding=encoding) as file:
            # Sniff the CSV dialect (delimiter, quotes, ...)
            try:
                dialect = csv.Sniffer().sniff(file.read(1024), list(',;:$ \t'))
            except csv.Error:
                dialect = csv.excel()
                dialect.delimiter = cls.DELIMITER
            finally:
                file.seek(0)
                dialect.skipinitialspace = True
            try:
                reader = csv.reader(file, dialect=dialect)
                return wrapper(cls.data_table(reader))
            except Exception as e:
                raise ValueError('Cannot parse dataset {}: {}'.format(filename, e))

    @classmethod
    def write_file(cls, filename, data):
        import csv
        with cls.open(filename, mode='wt', newline='', encoding='utf-8') as file:
            writer = csv.writer(file, delimiter=cls.DELIMITER)
            cls.write_headers(writer.writerow, data)
            writer.writerows(data)


class TabFormat(CSVFormat):
    EXTENSIONS = ('.tab', '.tsv')
    DESCRIPTION = 'Tab-separated values'
    DELIMITER = '\t'


class PickleFormat(FileFormat):
    EXTENSIONS = ('.pickle', '.pkl')
    DESCRIPTION = 'Pickled Python object file'

    @staticmethod
    def read_file(filename, wrapper=None):
        wrapper = wrapper or _IDENTITY
        import pickle
        with open(filename, 'rb') as f:
            return wrapper(pickle.load(f))

    @staticmethod
    def write_file(filename, data):
        import pickle
        with open(filename, 'wb') as f:
            pickle.dump(data, f, pickle.HIGHEST_PROTOCOL)


class BasketFormat(FileFormat):
    EXTENSIONS = ('.basket', '.bsk')
    DESCRIPTION = 'Basket file'

    @classmethod
    def read_file(cls, filename, storage_class=None):
        from Orange.data import _io, Table, Domain
        import sys
        if storage_class is None:
            storage_class = Table

        def constr_vars(inds):
            if inds:
                return [ContinuousVariable(x.decode("utf-8")) for _, x in
                        sorted((ind, name) for name, ind in inds.items())]

        X, Y, metas, attr_indices, class_indices, meta_indices = \
            _io.sparse_read_float(filename.encode(sys.getdefaultencoding()))

        attrs = constr_vars(attr_indices)
        classes = constr_vars(class_indices)
        meta_attrs = constr_vars(meta_indices)
        domain = Domain(attrs, classes, meta_attrs)
        return storage_class.from_numpy(
            domain, attrs and X, classes and Y, metas and meta_attrs)


class ExcelFormat(FileFormat):
    EXTENSIONS = ('.xls', '.xlsx')
    DESCRIPTION = 'Mircosoft Excel spreadsheet'

    @classmethod
    def read_file(cls, filename, wrapper=None):
        wrapper = wrapper or _IDENTITY
        filename, _, sheet_name = filename.rpartition(':')
        if not filename:
            filename, sheet_name = sheet_name, ''
        import xlrd
        wb = xlrd.open_workbook(filename, on_demand=True)
        for i in range(wb.nsheets):
            ss = wb.sheet_by_index(i)
            if sheet_name and ss.name != sheet_name:
                continue
            try:
                first_row = next(i for i in range(ss.nrows) if any(ss.row_values(i)))
                first_col = next(i for i in range(ss.ncols) if ss.cell_value(first_row, i))
                row_len = ss.row_len(first_row)
                cells = filter(any,
                               [[str(ss.cell_value(row, col)) if col < ss.row_len(row) else ''
                                 for col in range(first_col, row_len)]
                                for row in range(first_row, ss.nrows)])
                table = cls.data_table(cells)
            except Exception: continue
            break
        else:
            raise IOError('No sheets given for Excel document')
        return wrapper(table)


class DotFormat(FileFormat):
    EXTENSIONS = ('.dot', '.gv')
    DESCRIPTION = 'Dot graph description'
    SUPPORT_COMPRESSED = True

    @staticmethod
    def write_graph(cls, filename, graph):
        from sklearn import tree
        tree.export_graphviz(graph, out_file=cls.open(filename, 'wt'))

    @classmethod
    def write(cls, filename, tree):
        if type(tree) == dict:
            tree = tree['tree']
        cls.write_graph(filename, tree)
