import tarfile

import pytest

from io import BytesIO
from util.registry.streamlayerformat import StreamLayerMerger
from util.registry.aufs import AUFS_WHITEOUT
from util.registry.tarlayerformat import TarLayerReadException


def create_layer(*file_pairs):
    output = BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for current_filename, current_contents in file_pairs:
            if current_contents is None:
                # This is a deleted file.
                if current_filename.endswith("/"):
                    current_filename = current_filename[:-1]

                parts = current_filename.split("/")
                if len(parts) > 1:
                    current_filename = "/".join(parts[:-1]) + "/" + AUFS_WHITEOUT + parts[-1]
                else:
                    current_filename = AUFS_WHITEOUT + parts[-1]

                current_contents = b""

            if current_contents.startswith(b"linkto:"):
                info = tarfile.TarInfo(name=current_filename)
                info.linkname = current_contents[len(b"linkto:") :].decode("utf-8")
                info.type = tarfile.LNKTYPE
                tar.addfile(info)
            else:
                info = tarfile.TarInfo(name=current_filename)
                info.size = len(current_contents)
                tar.addfile(info, fileobj=BytesIO(current_contents))

    return output.getvalue()


def create_empty_layer():
    return b""


def squash_layers(layers, path_prefix=None):
    def getter_for_layer(layer):
        return lambda: BytesIO(layer)

    def layer_stream_getter():
        return [getter_for_layer(layer) for layer in layers]

    merger = StreamLayerMerger(layer_stream_getter, path_prefix=path_prefix)
    merged_data = b"".join(list(merger.get_generator()))
    return merged_data


def assertHasFile(squashed, filename, contents):
    with tarfile.open(fileobj=BytesIO(squashed), mode="r:*") as tar:
        member = tar.getmember(filename)
        assert contents == b"\n".join(tar.extractfile(member).readlines())


def assertDoesNotHaveFile(squashed, filename):
    with tarfile.open(fileobj=BytesIO(squashed), mode="r:*") as tar:
        try:
            member = tar.getmember(filename)
        except Exception as ex:
            return

        assert False, "Filename %s found" % filename


def test_single_layer():
    tar_layer = create_layer(
        ("some_file", b"foo"), ("another_file", b"bar"), ("third_file", b"meh")
    )

    squashed = squash_layers([tar_layer])

    assertHasFile(squashed, "some_file", b"foo")
    assertHasFile(squashed, "another_file", b"bar")
    assertHasFile(squashed, "third_file", b"meh")


def test_multiple_layers():
    second_layer = create_layer(
        ("some_file", b"foo"), ("another_file", b"bar"), ("third_file", b"meh")
    )

    first_layer = create_layer(("top_file", b"top"))

    squashed = squash_layers([first_layer, second_layer])

    assertHasFile(squashed, "some_file", b"foo")
    assertHasFile(squashed, "another_file", b"bar")
    assertHasFile(squashed, "third_file", b"meh")
    assertHasFile(squashed, "top_file", b"top")


def test_multiple_layers_dot():
    second_layer = create_layer(
        ("./some_file", b"foo"), ("another_file", b"bar"), ("./third_file", b"meh")
    )

    first_layer = create_layer(("top_file", b"top"))

    squashed = squash_layers([first_layer, second_layer])

    assertHasFile(squashed, "./some_file", b"foo")
    assertHasFile(squashed, "another_file", b"bar")
    assertHasFile(squashed, "./third_file", b"meh")
    assertHasFile(squashed, "top_file", b"top")


def test_multiple_layers_overwrite():
    second_layer = create_layer(
        ("some_file", b"foo"), ("another_file", b"bar"), ("third_file", b"meh")
    )

    first_layer = create_layer(("another_file", b"top"))

    squashed = squash_layers([first_layer, second_layer])

    assertHasFile(squashed, "some_file", b"foo")
    assertHasFile(squashed, "third_file", b"meh")
    assertHasFile(squashed, "another_file", b"top")


def test_multiple_layers_overwrite_base_dot():
    second_layer = create_layer(
        ("some_file", b"foo"), ("./another_file", b"bar"), ("third_file", b"meh")
    )

    first_layer = create_layer(("another_file", b"top"))

    squashed = squash_layers([first_layer, second_layer])

    assertHasFile(squashed, "some_file", b"foo")
    assertHasFile(squashed, "third_file", b"meh")
    assertHasFile(squashed, "another_file", b"top")
    assertDoesNotHaveFile(squashed, "./another_file")


def test_multiple_layers_overwrite_top_dot():
    second_layer = create_layer(
        ("some_file", b"foo"), ("another_file", b"bar"), ("third_file", b"meh")
    )

    first_layer = create_layer(("./another_file", b"top"))

    squashed = squash_layers([first_layer, second_layer])

    assertHasFile(squashed, "some_file", b"foo")
    assertHasFile(squashed, "third_file", b"meh")
    assertHasFile(squashed, "./another_file", b"top")
    assertDoesNotHaveFile(squashed, "another_file")


def test_deleted_file():
    second_layer = create_layer(
        ("some_file", b"foo"), ("another_file", b"bar"), ("third_file", b"meh")
    )

    first_layer = create_layer(("another_file", None))

    squashed = squash_layers([first_layer, second_layer])

    assertHasFile(squashed, "some_file", b"foo")
    assertHasFile(squashed, "third_file", b"meh")
    assertDoesNotHaveFile(squashed, "another_file")


def test_deleted_readded_file():
    third_layer = create_layer(("another_file", b"bar"))

    second_layer = create_layer(
        ("some_file", b"foo"), ("another_file", None), ("third_file", b"meh")
    )

    first_layer = create_layer(("another_file", b"newagain"))

    squashed = squash_layers([first_layer, second_layer, third_layer])

    assertHasFile(squashed, "some_file", b"foo")
    assertHasFile(squashed, "third_file", b"meh")
    assertHasFile(squashed, "another_file", b"newagain")


def test_deleted_in_lower_layer():
    third_layer = create_layer(("deleted_file", b"bar"))

    second_layer = create_layer(
        ("some_file", b"foo"), ("deleted_file", None), ("third_file", b"meh")
    )

    first_layer = create_layer(("top_file", b"top"))

    squashed = squash_layers([first_layer, second_layer, third_layer])

    assertHasFile(squashed, "some_file", b"foo")
    assertHasFile(squashed, "third_file", b"meh")
    assertHasFile(squashed, "top_file", b"top")
    assertDoesNotHaveFile(squashed, "deleted_file")


def test_deleted_in_lower_layer_with_added_dot():
    third_layer = create_layer(("./deleted_file", b"something"))

    second_layer = create_layer(("deleted_file", None))

    squashed = squash_layers([second_layer, third_layer])
    assertDoesNotHaveFile(squashed, "deleted_file")


def test_deleted_in_lower_layer_with_deleted_dot():
    third_layer = create_layer(("./deleted_file", b"something"))

    second_layer = create_layer(("./deleted_file", None))

    squashed = squash_layers([second_layer, third_layer])
    assertDoesNotHaveFile(squashed, "deleted_file")


def test_directory():
    second_layer = create_layer(("foo/some_file", b"foo"), ("foo/another_file", b"bar"))

    first_layer = create_layer(("foo/some_file", b"top"))

    squashed = squash_layers([first_layer, second_layer])

    assertHasFile(squashed, "foo/some_file", b"top")
    assertHasFile(squashed, "foo/another_file", b"bar")


def test_sub_directory():
    second_layer = create_layer(("foo/some_file", b"foo"), ("foo/bar/another_file", b"bar"))

    first_layer = create_layer(("foo/some_file", b"top"))

    squashed = squash_layers([first_layer, second_layer])

    assertHasFile(squashed, "foo/some_file", b"top")
    assertHasFile(squashed, "foo/bar/another_file", b"bar")


def test_delete_directory():
    second_layer = create_layer(("foo/some_file", b"foo"), ("foo/another_file", b"bar"))

    first_layer = create_layer(("foo/", None))

    squashed = squash_layers([first_layer, second_layer])

    assertDoesNotHaveFile(squashed, "foo/some_file")
    assertDoesNotHaveFile(squashed, "foo/another_file")


def test_delete_sub_directory():
    second_layer = create_layer(("foo/some_file", b"foo"), ("foo/bar/another_file", b"bar"))

    first_layer = create_layer(("foo/bar/", None))

    squashed = squash_layers([first_layer, second_layer])

    assertDoesNotHaveFile(squashed, "foo/bar/another_file")
    assertHasFile(squashed, "foo/some_file", b"foo")


def test_delete_sub_directory_with_dot():
    second_layer = create_layer(("foo/some_file", b"foo"), ("foo/bar/another_file", b"bar"))

    first_layer = create_layer(("./foo/bar/", None))

    squashed = squash_layers([first_layer, second_layer])

    assertDoesNotHaveFile(squashed, "foo/bar/another_file")
    assertHasFile(squashed, "foo/some_file", b"foo")


def test_delete_sub_directory_with_subdot():
    second_layer = create_layer(("./foo/some_file", b"foo"), ("./foo/bar/another_file", b"bar"))

    first_layer = create_layer(("foo/bar/", None))

    squashed = squash_layers([first_layer, second_layer])

    assertDoesNotHaveFile(squashed, "foo/bar/another_file")
    assertDoesNotHaveFile(squashed, "./foo/bar/another_file")
    assertHasFile(squashed, "./foo/some_file", b"foo")


def test_delete_directory_recreate():
    third_layer = create_layer(("foo/some_file", b"foo"), ("foo/another_file", b"bar"))

    second_layer = create_layer(("foo/", None))

    first_layer = create_layer(("foo/some_file", b"baz"))

    squashed = squash_layers([first_layer, second_layer, third_layer])

    assertHasFile(squashed, "foo/some_file", b"baz")
    assertDoesNotHaveFile(squashed, "foo/another_file")


def test_delete_directory_prefix():
    third_layer = create_layer(("foobar/some_file", b"foo"), ("foo/another_file", b"bar"))

    second_layer = create_layer(("foo/", None))

    squashed = squash_layers([second_layer, third_layer])

    assertHasFile(squashed, "foobar/some_file", b"foo")
    assertDoesNotHaveFile(squashed, "foo/another_file")


def test_delete_directory_pre_prefix():
    third_layer = create_layer(("foobar/baz/some_file", b"foo"), ("foo/another_file", b"bar"))

    second_layer = create_layer(("foo/", None))

    squashed = squash_layers([second_layer, third_layer])

    assertHasFile(squashed, "foobar/baz/some_file", b"foo")
    assertDoesNotHaveFile(squashed, "foo/another_file")


def test_delete_root_directory():
    third_layer = create_layer(("build/first_file", b"foo"), ("build/second_file", b"bar"))

    second_layer = create_layer(("build", None))

    squashed = squash_layers([second_layer, third_layer])

    assertDoesNotHaveFile(squashed, "build/first_file")
    assertDoesNotHaveFile(squashed, "build/second_file")


def test_tar_empty_layer():
    third_layer = create_layer(("build/first_file", b"foo"), ("build/second_file", b"bar"))

    empty_layer = create_layer()

    squashed = squash_layers([empty_layer, third_layer])

    assertHasFile(squashed, "build/first_file", b"foo")
    assertHasFile(squashed, "build/second_file", b"bar")


def test_data_empty_layer():
    third_layer = create_layer(("build/first_file", b"foo"), ("build/second_file", b"bar"))

    empty_layer = create_empty_layer()

    squashed = squash_layers([empty_layer, third_layer])

    assertHasFile(squashed, "build/first_file", b"foo")
    assertHasFile(squashed, "build/second_file", b"bar")


def test_broken_layer():
    third_layer = create_layer(("build/first_file", b"foo"), ("build/second_file", b"bar"))

    broken_layer = b"not valid data"

    with pytest.raises(TarLayerReadException):
        squash_layers([broken_layer, third_layer])


def test_single_layer_with_prefix():
    tar_layer = create_layer(
        ("some_file", b"foo"), ("another_file", b"bar"), ("third_file", b"meh")
    )

    squashed = squash_layers([tar_layer], path_prefix="foo/")

    assertHasFile(squashed, "foo/some_file", b"foo")
    assertHasFile(squashed, "foo/another_file", b"bar")
    assertHasFile(squashed, "foo/third_file", b"meh")


def test_multiple_layers_overwrite_with_prefix():
    second_layer = create_layer(
        ("some_file", b"foo"), ("another_file", b"bar"), ("third_file", b"meh")
    )

    first_layer = create_layer(("another_file", b"top"))

    squashed = squash_layers([first_layer, second_layer], path_prefix="foo/")

    assertHasFile(squashed, "foo/some_file", b"foo")
    assertHasFile(squashed, "foo/third_file", b"meh")
    assertHasFile(squashed, "foo/another_file", b"top")


def test_superlong_filename():
    tar_layer = create_layer(
        (
            "this_is_the_filename_that_never_ends_it_goes_on_and_on_my_friend_some_people_started",
            b"meh",
        )
    )

    squashed = squash_layers([tar_layer], path_prefix="foo/")
    assertHasFile(
        squashed,
        "foo/this_is_the_filename_that_never_ends_it_goes_on_and_on_my_friend_some_people_started",
        b"meh",
    )


def test_superlong_prefix():
    tar_layer = create_layer(
        ("some_file", b"foo"), ("another_file", b"bar"), ("third_file", b"meh")
    )

    squashed = squash_layers(
        [tar_layer],
        path_prefix="foo/bar/baz/something/foo/bar/baz/anotherthing/whatever/this/is/a/really/long/filename/that/goes/here/",
    )

    assertHasFile(
        squashed,
        "foo/bar/baz/something/foo/bar/baz/anotherthing/whatever/this/is/a/really/long/filename/that/goes/here/some_file",
        b"foo",
    )
    assertHasFile(
        squashed,
        "foo/bar/baz/something/foo/bar/baz/anotherthing/whatever/this/is/a/really/long/filename/that/goes/here/another_file",
        b"bar",
    )
    assertHasFile(
        squashed,
        "foo/bar/baz/something/foo/bar/baz/anotherthing/whatever/this/is/a/really/long/filename/that/goes/here/third_file",
        b"meh",
    )


def test_hardlink_to_deleted_file():
    first_layer = create_layer(
        ("tobedeletedfile", b"somecontents"),
        ("link_to_deleted_file", b"linkto:tobedeletedfile"),
        ("third_file", b"meh"),
    )

    second_layer = create_layer(("tobedeletedfile", None))

    squashed = squash_layers([second_layer, first_layer], path_prefix="foo/")

    assertHasFile(squashed, "foo/third_file", b"meh")
    assertHasFile(squashed, "foo/link_to_deleted_file", b"somecontents")
    assertDoesNotHaveFile(squashed, "foo/tobedeletedfile")


def test_multiple_hardlink_to_deleted_file():
    first_layer = create_layer(
        ("tobedeletedfile", b"somecontents"),
        ("link_to_deleted_file", b"linkto:tobedeletedfile"),
        ("another_link_to_deleted_file", b"linkto:tobedeletedfile"),
        ("third_file", b"meh"),
    )

    second_layer = create_layer(("tobedeletedfile", None))

    squashed = squash_layers([second_layer, first_layer], path_prefix="foo/")

    assertHasFile(squashed, "foo/third_file", b"meh")
    assertHasFile(squashed, "foo/link_to_deleted_file", b"somecontents")
    assertHasFile(squashed, "foo/another_link_to_deleted_file", b"somecontents")

    assertDoesNotHaveFile(squashed, "foo/tobedeletedfile")
