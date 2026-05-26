Non-source file. Must NOT contribute any symbols -- _SOURCE_SUFFIXES gates
this. If the walker ever started reading .txt files, this would smuggle
the word "class" below into the symbol set.

class ShouldNotBeFound { }
